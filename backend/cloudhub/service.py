"""F01/F03/F04: authorization, quota, durable commands and deadline fencing."""
import hashlib
import json
import re
import time
from copy import deepcopy
from .catalog import IMPLEMENTED, S3_CONFIG, SCOPES, configuration
from .cost import Estimator
from .errors import HubError, Conflict
from .portal import PortalMixin

def ident(prefix, *parts):
    return prefix + hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]

def record(kind, entity_id, owner, **fields):
    return {"pk": kind[0].upper() + "#" + entity_id, "kind": kind, "owner_id": owner,
            "owner_sort": kind + "#" + entity_id, **fields}

def expected(*rows):
    return {r["pk"]: r.get("version") for r in rows}

def retry(fn):
    for _ in range(12):
        try:
            return fn()
        except Conflict:
            time.sleep(0.002)
    raise HubError("CONCURRENT_UPDATE", "Có yêu cầu đồng thời; hãy thử lại với cùng Idempotency-Key.", 409)

class Service(PortalMixin):
    def __init__(self, store, clock=time.time, estimator=None, verified=None, validation=None,
                 user_limit=2, total_limit=4, budget_milliusd=0, workflow_terminal=None):
        self.store, self.clock = store, clock
        self.estimator = estimator or Estimator()
        self.verified = set(verified or []) & IMPLEMENTED
        self.validation = set(validation or []) & IMPLEMENTED
        self.user_limit, self.total_limit, self.budget = user_limit, total_limit, budget_milliusd
        self.workflow_terminal = workflow_terminal

    def owned(self, kind, entity_id, owner):
        row = self.store.get(kind + "#" + entity_id)
        if not row or row.get("owner_id") != owner:
            raise HubError("NOT_FOUND", "Không tìm thấy tài nguyên thuộc tài khoản của bạn.", 404)
        return row

    def validate_create(self, body, is_admin=False):
        if set(body) - {"template_id", "duration_hours", "configuration", "recovery_point", "validation_run"}:
            raise HubError("INVALID_INPUT", "Trường không được phép; owner_id được lấy từ Cognito.")
        tid, hours = body.get("template_id"), body.get("duration_hours")
        if tid not in IMPLEMENTED:
            raise HubError("TEMPLATE_UNAVAILABLE", "Mẫu chưa triển khai đầy đủ.", 409)
        validation_run = body.get("validation_run", False)
        if type(validation_run) is not bool:
            raise HubError("INVALID_INPUT", "validation_run phải là boolean.")
        if not (tid in self.verified or validation_run and is_admin and tid in self.validation):
            raise HubError("TEMPLATE_UNVERIFIED", "Mẫu chưa kiểm chứng AWS; chỉ quản trị được chạy vòng kiểm chứng đã duyệt.", 409)
        if type(hours) is not int or not 1 <= hours <= 8:
            raise HubError("INVALID_INPUT", "Thời lượng phải là số nguyên từ 1 đến 8 giờ.")
        config = body.get("configuration", {})
        if not isinstance(config, dict) or config not in ({}, configuration(tid)):
            raise HubError("INVALID_INPUT", "Chỉ hỗ trợ cấu hình chuẩn công bố trong danh mục mẫu.")
        quote = self.estimator.estimate(tid, hours)
        if not quote["approved"]:
            raise HubError("PRICES_UNAPPROVED", "Cần phê duyệt đơn giá và region trước khi tạo.", 409)
        return tid, hours, quote

    def idempotency(self, owner, key, scope, body):
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", key):
            raise HubError("IDEMPOTENCY_KEY_REQUIRED", "Cần Idempotency-Key dài 8–100 ký tự chữ, số, _ hoặc -.")
        pk = "I#" + ident("", owner, scope, key)
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        old = self.store.get(pk)
        if old and old["fingerprint"] != fingerprint:
            raise HubError("IDEMPOTENCY_MISMATCH", "Khóa này đã được dùng cho nội dung khác.", 409)
        return pk, fingerprint, old

    def quota(self, owner, amount, increase=1):
        user = self.store.get("Q#" + owner) or {"pk": "Q#" + owner, "active": 0}
        glob = self.store.get("Q#GLOBAL") or {"pk": "Q#GLOBAL", "active": 0, "reserved_milliusd": 0}
        if increase > 0 and (user["active"] + increase > user.get('max_active',self.user_limit) or glob["active"] + increase > self.total_limit):
            raise HubError("QUOTA_EXCEEDED", "Đã đạt quota môi trường.", 409)
        if amount > 0 and (glob["reserved_milliusd"] + amount > self.budget or user.get('reserved_milliusd',0)+amount > user.get('budget_milliusd',self.budget)):
            raise HubError("BUDGET_EXCEEDED", "Dự toán vượt ngân sách đã duyệt.", 409)
        user["active"] += increase
        glob["active"] += increase
        glob["reserved_milliusd"] += amount
        user['reserved_milliusd'] = user.get('reserved_milliusd',0)+amount
        return user, glob

    def create(self, owner, key, body, is_admin=False):
        tid, hours, quote = self.validate_create(body, is_admin)
        def transaction():
            ipk, fp, old = self.idempotency(owner, key, "create", body)
            if old:
                return self.owned("O", old["operation_id"], owner)
            now = int(self.clock())
            eid, oid = ident("env-", ipk), ident("op-", ipk)
            backup = None
            if body.get("recovery_point"):
                backup = self.owned("R", body["recovery_point"], owner)
                if backup["status"] != "AVAILABLE" or backup["retain_until"] <= now or backup.get("restore_lock") or backup.get('export_lock') or backup.get('delete_lock'):
                    raise HubError("RECOVERY_UNAVAILABLE", "Bản sao chưa sẵn sàng, hết hạn hoặc đang được khôi phục.", 409)
                if backup["template_id"] != tid:
                    raise HubError("INVALID_RECOVERY", "Bản sao thuộc mẫu khác.")
            user, glob = self.quota(owner, quote["total_milliusd"])
            env = record("environment", eid, owner, environment_id=eid, template_id=tid,
                status="PROVISIONING", duration_hours=hours, created_at=now, deadline=None,
                lock=oid, estimate=quote, resources=[], restore_from=body.get("recovery_point"),
                validation_run=body.get("validation_run", False), quota_released=False)
            op = record("operation", oid, owner, operation_id=oid, job_id=oid, environment_id=eid,
                action="restore" if backup else "create", status="QUEUED", phase="CREATE", created_at=now,
                attempts=0, events=[], recovery_point=body.get("recovery_point"))
            idem = {"pk": ipk, "fingerprint": fp, "operation_id": oid}
            rows = [user, glob, env, op, idem]
            if backup:
                backup["restore_lock"] = oid
                rows.append(backup)
            self.store.commit(rows, expected(*rows))
            return self.store.get(op["pk"])
        return retry(transaction)

    def command(self, owner, eid, action, key, body=None, due=False, admin_actor=None, reason=None):
        body = body or {}
        if body or action not in {"backup", "destroy", "audit"}:
            raise HubError("INVALID_INPUT", "Tác vụ không hợp lệ hoặc có trường không được phép.")
        def transaction():
            env = self.owned("E", eid, owner)
            ipk, fp, old = self.idempotency(owner, key, eid + "/" + action, {'actor':admin_actor,'reason':reason} if admin_actor else body)
            if old:
                return self.owned("O", old["operation_id"], owner)
            now = int(self.clock())
            if due and (env["status"] != "READY" or env["deadline"] > now):
                return None
            if env.get("lock"):
                raise HubError("OPERATION_LOCKED", "Môi trường đang có tác vụ.", 409)
            allowed = {"backup": {"READY"}, "destroy": {"READY", "ERROR_RETAINED", "CREATE_FAILED"},
                       "audit": {"DELETED", "RESIDUAL", "ERROR_RETAINED", "CREATE_FAILED"}}
            if env["status"] not in allowed[action]:
                raise HubError("INVALID_STATE", "Trạng thái không cho phép tác vụ này.", 409)
            oid = ident("op-", ipk)
            rp = ident("rp-", oid) if action in {"backup", "destroy"} else None
            op = record("operation", oid, owner, operation_id=oid, job_id=oid, environment_id=eid,
                action=action, status="QUEUED", phase="AUDIT" if action == "audit" else "BACKUP",
                created_at=now, attempts=0, events=[], recovery_point=rp)
            if admin_actor:
                op.update(admin_actor=admin_actor,admin_reason=reason)
            # Failed provisioning with no instance may be cleaned without backing up nonexistent data.
            if action == "destroy" and env["status"] == "CREATE_FAILED" and not (env.get("instance_id") or env.get("bucket_name") or env.get("source_ids")):
                op["phase"], op["recovery_point"] = "DELETE", None
                rp = None
            env["lock"] = oid
            if due:
                env["expiry_triggered_at"] = env.get("expiry_triggered_at", now)
            env["status"] = {"backup": "BACKING_UP", "destroy": "RETIRING", "audit": env["status"]}[action]
            rows = [env, op, {"pk": ipk, "fingerprint": fp, "operation_id": oid}]
            if action == "backup":
                # Extra backups reserve a full session estimate conservatively.
                quote = self.estimator.estimate(env["template_id"], 1)
                if not quote["approved"]:
                    raise HubError("PRICES_UNAPPROVED", "Chưa phê duyệt chi phí bản sao.", 409)
                user, glob = self.quota(owner, quote["total_milliusd"], 0)
                counter = self.store.get("B#" + eid) or {"pk": "B#" + eid, "count": 0}
                if counter["count"] >= 3:
                    raise HubError("BACKUP_QUOTA", "Tối đa 3 bản sao thủ công cho một môi trường.", 409)
                counter["count"] += 1
                rows.extend([user, glob, counter])
            if rp:
                rows.append(record("recovery", rp, owner, recovery_point=rp, environment_id=eid,
                    template_id=env["template_id"], status="PENDING", source=env.get('data_source') or env.get("bucket_name") or env.get("data_volume_id"),
                    created_at=now, retain_until=now + 7 * 86400,
                    scope=SCOPES[env['template_id']],
                    restore_verified=False))
            self.store.commit(rows, expected(*rows))
            return self.store.get(op["pk"])
        return retry(transaction)

    def data_command(self, owner, eid, action, key, body):
        if action not in {'put_record','list_records','load_dataset','query_demo'}:
            raise HubError('INVALID_INPUT','Tác vụ dữ liệu không được hỗ trợ.')
        if action in {'put_record','load_dataset'}:
            if set(body)!={'record_id','text'} or not isinstance(body['record_id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',body['record_id']) or not isinstance(body['text'],str) or len(body['text'].encode())>256 or any(ord(c)<32 and c not in '\n\t' for c in body['text']):
                raise HubError('INVALID_INPUT','record_id tối đa 64 ký tự; text tối đa 256 byte, không có ký tự điều khiển.')
        elif body:
            raise HubError('INVALID_INPUT','Tác vụ đọc dữ liệu dùng object rỗng.')
        def transaction():
            env=self.owned('E',eid,owner)
            supported={'T03':{'put_record','list_records'},'T04':{'put_record','list_records'},'T08':{'put_record','list_records'},'T09':{'put_record','list_records'},'T10':{'load_dataset','query_demo'}}
            if action not in supported.get(env['template_id'],set()):raise HubError('UNSUPPORTED_OPERATION','Mẫu không hỗ trợ tác vụ dữ liệu này.',409)
            ipk,fp,old=self.idempotency(owner,key,eid+'/'+action,body)
            if old:return self.owned('O',old['operation_id'],owner)
            if env['status']!='READY' or env.get('lock') or env['deadline']<=int(self.clock()):raise HubError('OPERATION_LOCKED','Môi trường hết hạn hoặc đang có tác vụ.',409)
            if env.get('data_commands',0)>=128:raise HubError('DATA_QUOTA','Tối đa 128 tác vụ dữ liệu mỗi phiên.',409)
            oid=ident('op-',ipk);env['lock']=oid;env['data_commands']=env.get('data_commands',0)+1
            op=record('operation',oid,owner,operation_id=oid,job_id=oid,environment_id=eid,action=action,status='QUEUED',phase='USE_DATA',
                created_at=int(self.clock()),attempts=0,events=[],data=deepcopy(body))
            rows=[env,op,{'pk':ipk,'fingerprint':fp,'operation_id':oid}]
            self.store.commit(rows,expected(*rows));return self.store.get(op['pk'])
        return retry(transaction)

    def write_file(self, owner, eid, key, body):
        from .s3_files import validate_file
        validate_file(body)
        def transaction():
            env = self.owned("E", eid, owner)
            ipk, fp, old = self.idempotency(owner, key, eid + "/files", body)
            if old:
                return self.owned("O", old["operation_id"], owner)
            if env["template_id"] not in {'T02','T06','T07'}:
                raise HubError("UNSUPPORTED_OPERATION", "Mẫu này không dùng kho file S3.", 409)
            if env["status"] != "READY" or env.get("lock") or env["deadline"] <= int(self.clock()):
                raise HubError("OPERATION_LOCKED", "Môi trường hết hạn hoặc đang có tác vụ; chưa thể đăng file.", 409)
            if env.get("file_writes", 0) >= S3_CONFIG["max_writes"]:
                raise HubError("FILE_QUOTA", "Đã đạt giới hạn số lần đăng file trong phiên.", 409)
            oid = ident("op-", ipk)
            env["lock"], env["file_writes"] = oid, env.get("file_writes", 0) + 1
            op = record("operation", oid, owner, operation_id=oid, job_id=oid, environment_id=eid,
                action="write_file", status="QUEUED", phase="WRITE_FILE", created_at=int(self.clock()),
                attempts=0, events=[], file=deepcopy(body))
            rows = [env, op, {"pk": ipk, "fingerprint": fp, "operation_id": oid}]
            self.store.commit(rows, expected(*rows))
            return self.store.get(op["pk"])
        return retry(transaction)

    def extend(self, owner, eid, key, body):
        if set(body) != {"hours"} or type(body["hours"]) is not int or not 1 <= body["hours"] <= 4:
            raise HubError("INVALID_INPUT", "Gia hạn từ 1 đến 4 giờ.")
        def transaction():
            env = self.owned("E", eid, owner)
            ipk, fp, old = self.idempotency(owner, key, eid + "/extend", body)
            if old:
                return old["response"]
            if env["status"] != "READY" or env.get("lock") or env["deadline"] <= int(self.clock()):
                raise HubError("INVALID_STATE", "Không thể gia hạn phiên đang xử lý hoặc đã hết hạn.", 409)
            if env["duration_hours"] + body["hours"] > 8:
                raise HubError("DURATION_LIMIT", "Tổng thời lượng tối đa 8 giờ.")
            quote = self.estimator.estimate(env["template_id"], body["hours"])
            if not quote["approved"]:
                raise HubError("PRICES_UNAPPROVED", "Chưa phê duyệt đơn giá.", 409)
            user, glob = self.quota(owner, quote["total_milliusd"], 0)
            env["deadline"] += body["hours"] * 3600
            env["duration_hours"] += body["hours"]
            env["schedule_pending"] = True
            env["estimate"]["total_milliusd"] += quote["total_milliusd"]
            response = {"environment_id": eid, "deadline": env["deadline"], "schedule_pending": True}
            rows = [env, user, glob, {"pk": ipk, "fingerprint": fp, "response": response}]
            self.store.commit(rows, expected(*rows))
            return response
        return retry(transaction)

    def expire(self, eid):
        env = self.store.get("E#" + eid)
        if not env or env["deadline"] is None or env["deadline"] > int(self.clock()):
            return None
        if env["status"] == "BACKING_UP":
            # Record activation while an existing backup owns the lock. It will hand off
            # to retirement after resume; activation time differs from deletion time.
            env["expiry_triggered_at"] = env.get("expiry_triggered_at", int(self.clock()))
            env["expiry_pending"] = True
            self.save(env)
            return None
        if env["status"] != "READY":
            return None
        # Recheck deadline and lock inside the SAME transaction as the retirement command.
        return self.command(env["owner_id"], eid, "destroy", "expiry_" + str(env["deadline"]), due=True)

    def save(self, *rows):
        self.store.commit(list(rows), expected(*rows))

    def finish(self, op, env, status="SUCCEEDED"):
        op["status"], op["finished_at"] = status, int(self.clock())
        env["lock"] = None
        if status=='SUCCEEDED' and op['action'] in {'create','restore','backup','write_file','put_record','list_records','load_dataset','query_demo'}:
            env['status']='READY';env['needs_admin']=False
        rows = [op, env]
        if op["action"] == "restore":
            rp = self.store.get("R#" + op["recovery_point"])
            rp["restore_lock"] = None
            if status == "SUCCEEDED":
                rp["restore_verified"] = True
                rp["verified_environment_id"] = env["environment_id"]
                rp["verified_at"] = int(self.clock())
            rows.append(rp)
        if env["status"] == "DELETED" and not env["quota_released"]:
            user, glob = self.quota(env["owner_id"], 0, -1)
            env["quota_released"] = True
            rows.extend([user, glob])
        self.save(*rows)
