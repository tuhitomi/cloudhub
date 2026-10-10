"""One durable transition per invocation; no sleeping inside Lambda."""
import json
import logging
from .errors import Conflict, HubError

log = logging.getLogger(__name__)
RUNTIME = None

class Worker:
    def __init__(self, service, driver):
        self.service, self.driver = service, driver

    def tick(self, oid, generation=None):
        s = self.service
        op = s.store.get("O#" + oid)
        if not op:
            raise HubError("NOT_FOUND", "Không tìm thấy tác vụ.", 404)
        if generation is not None and generation != op.get('generation',0):
            return {'operation_id':oid,'done':True,'failed':False,'generation':generation}
        if op["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return {"operation_id": oid, "done": True, "failed": op["status"] == "FAILED"}
        if op['action'] in {'delete_backup','export_backup','cost'}:
            return self.product_tick(op)
        env = s.store.get("E#" + op["environment_id"])
        if env.get("lock") != oid:
            raise HubError("LOCK_LOST", "Khóa tác vụ đã thay đổi.", 409)
        op["status"] = "RUNNING"
        op["attempts"] += 1
        if op["attempts"] > 180:
            return self.fail(oid, "OPERATION_TIMEOUT: exceeded 180 polls")
        old_phase = op["phase"]
        try:
            if op["phase"] == "USE_DATA":
                if not self.driver.use_data(env, op):
                    s.save(op, env)
                    return {"operation_id": oid, "done": False, "failed": False}
                s.finish(op, env)
                expiry = s.expire(env["environment_id"])
                return {"operation_id": oid, "done": True, "failed": False,
                        "expiry_operation_id": expiry["operation_id"] if expiry else None}
            elif op["phase"] == "WRITE_FILE":
                self.driver.write_file(env, op)
                s.finish(op, env)
                expiry = s.expire(env["environment_id"])
                return {"operation_id": oid, "done": True, "failed": False,
                        "expiry_operation_id": expiry["operation_id"] if expiry else None}
            elif op["phase"] == "CREATE":
                recovery = s.store.get("R#" + op["recovery_point"]) if op["action"] == "restore" else None
                self.driver.create(env, op, recovery)
                op["phase"] = "WAIT_CREATE"
            elif op["phase"] == "WAIT_CREATE":
                if self.driver.created(env):
                    op["phase"] = "READINESS"
            elif op["phase"] == "READINESS":
                recovery = s.store.get("R#" + op["recovery_point"]) if op["action"] == "restore" else None
                if self.driver.ready(env, op, recovery):
                    op["phase"] = "SCHEDULE"
                    # Session begins ONLY when data readiness succeeds.
                    env["ready_at"] = int(s.clock())
                    env["deadline"] = env["ready_at"] + env["duration_hours"] * 3600
            elif op["phase"] == "SCHEDULE":
                self.driver.schedule(env)
                env["status"], env["schedule_pending"] = "READY", False
                s.finish(op, env)
                return {"operation_id": oid, "done": True, "failed": False}
            elif op["phase"] == "BACKUP":
                rp = s.store.get("R#" + op["recovery_point"])
                if op['action'] == 'destroy':
                    rp['retirement_lock'] = oid
                if self.driver.backup(env, op, rp):
                    rp["status"], rp["completed_at"] = "AVAILABLE", int(s.clock())
                    op["phase"] = "DELETE" if op["action"] == "destroy" else "RESUME"
                s.save(op, env, rp)
                return {"operation_id": oid, "done": False, "failed": False}
            elif op["phase"] == "RESUME":
                self.driver.resume(env)
                # Check SSM/EC2 again after an intentional backup outage.
                op.pop("ready_command", None)
                op["phase"] = "WAIT_RESUME"
            elif op["phase"] == "WAIT_RESUME":
                if self.driver.ready(env, op, None):
                    env["status"] = "READY"
                    env["needs_admin"] = False
                    s.finish(op, env)
                    expiry = s.expire(env["environment_id"])
                    return {"operation_id": oid, "done": True, "failed": False,
                            "expiry_operation_id": expiry["operation_id"] if expiry else None}
            elif op["phase"] == "DELETE":
                if op.get('recovery_point'):
                    s.retirement_recovery(op, env)
                else:
                    # CreateStack may have succeeded even when its HTTP response was lost.
                    # Never infer "no source" from incomplete DynamoDB metadata.
                    self.driver.inventory(env)
                    if env.get("instance_id") or env.get("data_volume_id") or env.get("bucket_name") or env.get("source_ids"):
                        raise HubError("SOURCE_DISCOVERED_NEEDS_BACKUP", "Tìm thấy nguồn AWS chưa sao lưu; giữ lại để xử lý an toàn.", 409)
                if self.driver.delete(env, op):
                    op["phase"] = "AUDIT"
            elif op["phase"] == "AUDIT":
                backups = [r for r in s.store.scan_kind("recovery") if r["environment_id"] == env["environment_id"]]
                report = self.driver.audit(env, backups)
                report["checked_at"] = int(s.clock())
                env["cleanup_report"] = report
                env["status"] = "RESIDUAL" if report["residual"] or report["unknown"] else "DELETED"
                if env['status']=='DELETED':env['retired_at']=env.get('retired_at',int(s.clock()))
                env["needs_admin"] = env["status"] == "RESIDUAL"
                residual = env['needs_admin']
                if residual:
                    op['error'] = 'CLEANUP_RESIDUAL: còn tài nguyên dư hoặc chưa xác minh được; giữ hạn mức và bản sao.'
                s.finish(op, env, 'FAILED' if residual else 'SUCCEEDED')
                if residual:
                    print(json.dumps({'event': 'CloudHubCleanupResidual', 'operation_id': oid,
                                      'environment_id': env['environment_id'],
                                      'residual_count': len(report['residual']), 'unknown_count': len(report['unknown'])}))
                return {"operation_id": oid, "done": True, "failed": residual}
            else:
                raise HubError("INVALID_PHASE", "Trạng thái tác vụ không xác định.", 409)
            if old_phase != op["phase"]:
                op["events"] = (op["events"] + [{"phase": op["phase"], "at": int(s.clock())}])[-30:]
            s.save(op, env)
        except Conflict:
            raise
        except HubError as exc:
            # Persist partial inventory before marking failure.
            op["error"] = exc.code + ": " + exc.message
            s.save(op, env)
            return self.fail(oid, op["error"])
        return {"operation_id": oid, "done": False, "failed": False}

    def product_tick(self, op):
        s=self.service;env=s.store.get('E#'+op['environment_id'])
        op['status']='RUNNING';op['attempts']+=1
        if op['attempts']>180:return self.fail(op['operation_id'],'OPERATION_TIMEOUT')
        rp=None
        try:
            if op['action']=='cost':
                if env.get('cost_lock')!=op['operation_id']:raise HubError('LOCK_LOST','Khóa đối soát đã thay đổi.',409)
                from .actual_cost import ActualCosts
                import os
                report=ActualCosts(os.getenv('ACTUAL_COSTS_ENABLED')=='true').report(env,s.clock())
                env['cost_report']=report;env['cost_lock']=None;op['result']=report
                rows=[env,op]
            else:
                rp=s.store.get('R#'+op['recovery_point'])
                lock='delete_lock' if op['action']=='delete_backup' else 'export_lock'
                if rp.get(lock)!=op['operation_id'] or rp.get('restore_lock'):
                    raise HubError('RECOVERY_LOCKED','Khóa bản sao đã thay đổi.',409)
                if op['action']=='delete_backup':
                    if rp.get('retirement_lock'):
                        raise HubError('RECOVERY_LOCKED','Bản sao đang bảo vệ nguồn chưa thu hồi sạch.',409)
                    if not self.driver.expire_backup(rp):
                        s.save(op,rp);return {'operation_id':op['operation_id'],'done':False,'failed':False}
                    rp.update(status='DELETED',deleted_at=int(s.clock()),delete_lock=None)
                else:
                    from .backup_export import BackupExports
                    if rp['status']!='AVAILABLE' or rp['retain_until']<=s.clock():
                        raise HubError('RECOVERY_UNAVAILABLE','Bản sao đã hết hạn.',409)
                    op['result']=BackupExports(self.driver.settings,self.driver).export(env,op,rp)
                    rp['export_lock']=None
                rows=[rp,op]
            op.update(status='SUCCEEDED',finished_at=int(s.clock()))
            s.save(*rows)
            return {'operation_id':op['operation_id'],'done':True,'failed':False}
        except HubError as exc:
            return self.fail(op['operation_id'],exc.code+': '+exc.message)

    def fail(self, oid, error, generation=None):
        s = self.service
        op = s.store.get("O#" + oid)
        if not op or op["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"} or generation is not None and generation != op.get('generation',0):
            return {"operation_id": oid, "done": True, "failed": True}
        env = s.store.get("E#" + op["environment_id"])
        op["error"] = str(error)[:1500]
        op['failed_phase']=op['phase']
        if op['action'] in {'delete_backup','export_backup','cost'}:
            rows=[op]
            if op['action']=='cost':
                if env.get('cost_lock')==oid:env['cost_lock']=None
                rows.append(env)
            else:
                rp=s.store.get('R#'+op['recovery_point'])
                lock='delete_lock' if op['action']=='delete_backup' else 'export_lock'
                if rp.get(lock)==oid:rp[lock]=None
                if op['action']=='delete_backup':rp['status']='DELETE_FAILED'
                rp['error']=op['error'];rows.append(rp)
            op.update(status='FAILED',finished_at=int(s.clock()))
            s.save(*rows)
            print(json.dumps({'event':'CloudHubOperationFailed','operation_id':oid,'error':op['error']}))
            return {'operation_id':oid,'done':True,'failed':True}
        env["needs_admin"] = True
        env["status"] = "CREATE_FAILED" if op["action"] in {"create", "restore"} else "ERROR_RETAINED"
        if op["action"] in {"backup", "destroy"} and op.get("recovery_point"):
            rp = s.store.get("R#" + op["recovery_point"])
            if rp and rp["status"] not in {'AVAILABLE', 'DELETED', 'EXPIRED', 'DELETING'}:
                rp["status"], rp["error"] = "FAILED", op["error"]
                s.save(rp)
            # Source retained. Attempt restart only; a restart failure is also recorded.
            try:
                self.driver.resume(env)
            except Exception as exc:
                env["resume_error"] = type(exc).__name__
        s.finish(op, env, "FAILED")
        # CloudWatch metric filter drives a real alarm; no notification claim without subscription.
        print(json.dumps({"event": "CloudHubOperationFailed", "operation_id": oid,
                          "environment_id": env["environment_id"], "error": op["error"]}))
        return {"operation_id": oid, "done": True, "failed": True}

def handler(event, context=None):
    global RUNTIME
    if RUNTIME is None:
        from .runtime import build_service, build_driver
        RUNTIME = Worker(build_service(), build_driver())
    if event.get("failure"):
        return RUNTIME.fail(event["operation_id"], event["failure"],event.get('generation'))
    result = RUNTIME.tick(event["operation_id"],event.get('generation'))
    result['generation']=event.get('generation',0)
    if result.get("expiry_operation_id"):
        from .runtime import dispatch
        dispatch(result["expiry_operation_id"])
    return result
