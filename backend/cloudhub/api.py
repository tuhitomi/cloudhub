"""HTTP API v2 Lambda adapter; trust only API Gateway validated JWT claims."""
import base64
import json
import logging
import os
import re
from decimal import Decimal
from .catalog import catalog
from .catalog import IMPLEMENTED
from .errors import HubError

log = logging.getLogger(__name__)
SERVICE = None
DISPATCH = None

def serialize(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral() else str(value)
    raise TypeError(type(value).__name__)

def public(row):
    result={k: v for k, v in row.items() if k not in {"pk", "version", "owner_sort", "restore_lock", "file",'data','warehouse_secret'}}
    if row.get('kind')=='recovery':result['in_use']=bool(row.get('restore_lock') or row.get('export_lock') or row.get('delete_lock'))
    return result

def handler(event, context=None):
    global SERVICE, DISPATCH
    if SERVICE is None:
        from .runtime import build_service, dispatch
        SERVICE, DISPATCH = build_service(), dispatch
    rid = event.get("requestContext", {}).get("requestId", "local")
    try:
        claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
        owner = claims.get("sub")
        if not owner or claims.get("token_use") != "access":
            raise HubError("UNAUTHORIZED", "Cần access token Cognito hợp lệ.", 401)
        groups = claims.get("cognito:groups", "")
        if isinstance(groups, str):
            groups = re.findall(r"[A-Za-z0-9_-]+", groups)
        admin = "admin" in groups
        account = SERVICE.store.get('U#'+owner)
        if account and account.get('enabled') is False:
            raise HubError('ACCOUNT_DISABLED','Tài khoản đã bị khóa.',403)
        method = event["requestContext"]["http"]["method"]
        path = event.get("rawPath", "/")
        headers = {k.lower(): v for k, v in event.get("headers", {}).items()}
        raw = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode()
        if len(raw) > 16384:
            raise HubError("INVALID_INPUT", "Yêu cầu quá lớn.", 413)
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise HubError("INVALID_INPUT", "Nội dung phải là JSON object.")
        key = headers.get("idempotency-key")
        status = 200
        from .portal_api import route, UNHANDLED
        extra = route(SERVICE,method,path,body,key,owner,admin,claims,event.get('queryStringParameters') or {},public)
        if extra is not UNHANDLED:
            result,status=extra
        elif method == "GET" and path == "/templates":
            result = {"items": catalog(SERVICE.verified)}
        elif method == "POST" and path == "/estimates":
            if set(body) != {"template_id", "duration_hours"} or body["template_id"] not in IMPLEMENTED or type(body["duration_hours"]) is not int or not 1 <= body["duration_hours"] <= 8:
                raise HubError("INVALID_INPUT", "Chọn mẫu có dự toán và thời lượng 1–8 giờ.")
            result = SERVICE.estimator.estimate(body["template_id"], body["duration_hours"])
        elif method == "POST" and path == "/environments":
            result, status = public(SERVICE.create(owner, key, body, admin)), 202
        elif method == "GET" and path in {"/environments", "/recovery-points"}:
            kind = "environment" if path == "/environments" else "recovery"
            result = {"items": [public(r) for r in SERVICE.store.list_owner(owner, kind)]}
        elif method == "GET" and path == "/admin/attention":
            if not admin:
                raise HubError("FORBIDDEN", "Cần quyền quản trị.", 403)
            result = {"items": [public(r) for r in SERVICE.store.scan_kind("environment") if r.get("needs_admin") or r["status"] == "RESIDUAL"]}
        elif method == "GET" and re.fullmatch(r"/environments/[a-z0-9-]+/ssh-key", path):
            from .access import ssh_key
            result = ssh_key(SERVICE, owner, path.strip("/").split("/")[1])
        elif method in {"GET", "POST"} and re.fullmatch(r"/environments/[a-z0-9-]+/files", path):
            eid = path.strip("/").split("/")[1]
            if method == "POST":
                result, status = public(SERVICE.write_file(owner, eid, key, body)), 202
            else:
                from .s3_files import OwnedFiles
                result = OwnedFiles(SERVICE, json.loads(os.environ["DRIVER_SETTINGS"])).list(owner, eid)
        elif method == "GET" and re.fullmatch(r"/(environments|operations|recovery-points)/[a-z0-9-]+", path):
            group, entity_id = path.strip("/").split("/")
            kind = {"environments": "E", "operations": "O", "recovery-points": "R"}[group]
            result = public(SERVICE.owned(kind, entity_id, owner))
        elif method=='POST' and re.fullmatch(r'/environments/[a-z0-9-]+/share',path):
            from .s3_files import OwnedFiles
            result=OwnedFiles(SERVICE,json.loads(os.environ['DRIVER_SETTINGS'])).share(owner,path.strip('/').split('/')[1],body)
        elif method=='POST' and re.fullmatch(r'/environments/[a-z0-9-]+/(put_record|list_records|load_dataset|query_demo)',path):
            _,eid,action=path.strip('/').split('/')
            result,status=public(SERVICE.data_command(owner,eid,action,key,body)),202
        elif method == "POST" and re.fullmatch(r"/environments/[a-z0-9-]+/(backup|destroy|audit|extend)", path):
            _, eid, action = path.strip("/").split("/")
            if action == "extend":
                result = SERVICE.extend(owner, eid, key, body)
            else:
                result, status = public(SERVICE.command(owner, eid, action, key, body)), 202
        else:
            raise HubError("NOT_FOUND", "Đường dẫn không tồn tại.", 404)
        if status == 202 and DISPATCH:
            try:
                DISPATCH(result["operation_id"])
            except Exception:
                # Durable outbox: sweeper retries dispatch, same execution name/input.
                log.exception("Dispatch deferred for %s", result["operation_id"])
        return response(status, result)
    except (ValueError, UnicodeError, KeyError, TypeError):
        return response(400, {"error": {"code": "INVALID_INPUT", "message": "Yêu cầu không hợp lệ.", "request_id": rid}})
    except HubError as exc:
        return response(exc.status, {"error": {"code": exc.code, "message": exc.message, "request_id": rid}})
    except Exception:
        log.exception("Internal error request=%s", rid)
        return response(500, {"error": {"code": "INTERNAL_ERROR", "message": "Lỗi xử lý; giữ nguyên Idempotency-Key khi thử lại.", "request_id": rid}})

def response(status, body):
    return {"statusCode": status, "headers": {"content-type": "application/json; charset=utf-8", "cache-control": "no-store"},
            "body": json.dumps(body, ensure_ascii=False, default=serialize)}
