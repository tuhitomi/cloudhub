"""Small approved files only. Browser never receives write credentials or upload URLs."""
import base64
import hashlib
import re
from .catalog import S3_CONFIG
from .errors import HubError

MIME_TYPES = {"text/html", "text/css", "text/plain", "application/javascript", "application/json",
              "image/png", "image/jpeg", "image/svg+xml"}

def error_code(exc):
    return getattr(exc, "response", {}).get("Error", {}).get("Code", "")

def validate_key(key):
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", key) or any(
            part in {"", ".", "..", ".cloudhub"} for part in key.split("/")):
        raise HubError("INVALID_INPUT", "Tên file tối đa 128 ký tự; không dùng đường dẫn ngược hoặc .cloudhub.")
    return key

def validate_file(body):
    if not isinstance(body, dict) or set(body) != {"key", "content_base64", "content_type"}:
        raise HubError("INVALID_INPUT", "Cần key, content_base64 và content_type.")
    validate_key(body["key"])
    if body["content_type"] not in MIME_TYPES or not isinstance(body["content_base64"], str):
        raise HubError("INVALID_INPUT", "Định dạng file chưa được hỗ trợ.")
    try:
        data = base64.b64decode(body["content_base64"], validate=True)
    except (ValueError, TypeError):
        raise HubError("INVALID_INPUT", "Nội dung base64 không hợp lệ.")
    if len(data) > S3_CONFIG["max_file_bytes"]:
        raise HubError("FILE_QUOTA", "Mỗi file tối đa 8 KiB.", 413)
    return data

def bucket_name(env, settings):
    eid = env["environment_id"]
    if not re.fullmatch(r"env-[a-f0-9]{24}", eid):
        raise HubError("RESOURCE_SCOPE_MISMATCH", "ID môi trường không hợp lệ.", 409)
    return "cloudhub-" + settings["account_id"] + "-" + settings["region"] + "-" + eid[4:]

def assert_bucket(client, env, settings):
    name = bucket_name(env, settings)
    if env.get("bucket_name") and env["bucket_name"] != name:
        raise HubError("RESOURCE_SCOPE_MISMATCH", "Tên bucket không thuộc môi trường.", 409)
    try:
        tags = client.get_bucket_tagging(Bucket=name, ExpectedBucketOwner=settings["account_id"])["TagSet"]
    except Exception as exc:
        if error_code(exc) == "NoSuchBucket":
            return None
        raise
    tags = {t["Key"]: t["Value"] for t in tags}
    if tags.get("cloudhub:environment") != env["environment_id"] or tags.get("cloudhub:owner") != env["owner_id"] or tags.get("cloudhub:managed") != "true":
        raise HubError("RESOURCE_SCOPE_MISMATCH", "Bucket không có nhãn sở hữu phù hợp; giữ nguyên tài nguyên.", 409)
    return name

def objects(client, name, settings):
    result = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=name, ExpectedBucketOwner=settings["account_id"]):
        for item in page.get("Contents", []):
            if item["Key"].startswith(".cloudhub/"):
                continue
            validate_key(item["Key"])
            if item["Size"] > S3_CONFIG["max_file_bytes"]:
                raise HubError("FILE_QUOTA", "Nguồn có file vượt cấu hình; giữ dữ liệu để quản trị kiểm tra.", 409)
            result.append({"key": item["Key"], "size": item["Size"]})
            if len(result) > S3_CONFIG["max_files"]:
                raise HubError("FILE_QUOTA", "Nguồn có quá nhiều file; giữ nguyên dữ liệu.", 409)
    if sum(r["size"] for r in result) > S3_CONFIG["max_total_bytes"]:
        raise HubError("FILE_QUOTA", "Tổng dữ liệu vượt 256 KiB.", 409)
    return sorted(result, key=lambda r: r["key"])

def read(client, bucket, key, account, version=None):
    params = {"Bucket": bucket, "Key": key, "ExpectedBucketOwner": account}
    if version:
        params["VersionId"] = version
    response = client.get_object(**params)
    stream = response["Body"]
    try:
        content = stream.read(S3_CONFIG["max_file_bytes"] + 1)
    finally:
        stream.close()
    if len(content) > S3_CONFIG["max_file_bytes"]:
        raise HubError("FILE_QUOTA", "File vượt 8 KiB.", 409)
    return content, response

class OwnedFiles:
    def __init__(self, service, settings, client=None):
        self.service, self.settings = service, settings
        if client is None:
            import boto3
            from botocore.config import Config
            client = boto3.client("s3", region_name=settings.get('region'), config=Config(signature_version='s3v4',
                s3={'addressing_style':'virtual'}, connect_timeout=1, read_timeout=2, retries={"total_max_attempts": 1}))
        self.client = client

    def list(self, owner, eid):
        env = self.service.owned("E", eid, owner)
        if env["template_id"] not in {'T02','T06','T07','T10'} or env["status"] != "READY" or env["deadline"] <= self.service.clock():
            raise HubError("INVALID_STATE", "Kho file chưa sẵn sàng hoặc phiên đã hết hạn.", 409)
        name = assert_bucket(self.client, env, self.settings)
        if not name:
            raise HubError("SOURCE_MISSING", "Không tìm thấy bucket nguồn.", 409)
        return {"items": objects(self.client, name, self.settings), "limits": S3_CONFIG,
                "writes_used": env.get("file_writes", 0)}

    def share(self,owner,eid,body):
        if set(body)!={'key'}:raise HubError('INVALID_INPUT','Cần tên file key.')
        key=validate_key(body['key']);env=self.service.owned('E',eid,owner)
        if env['template_id']!='T07' or env['status']!='READY' or env.get('lock') or env['deadline']<=self.service.clock():
            raise HubError('INVALID_STATE','Kho file chưa sẵn sàng hoặc đang xử lý.',409)
        bucket=assert_bucket(self.client,env,self.settings)
        if not bucket:raise HubError('SOURCE_MISSING','Không tìm thấy kho file.',409)
        metadata=self.client.head_object(Bucket=bucket,Key=key,ExpectedBucketOwner=self.settings['account_id'])
        seconds=min(900,int(float(env['deadline'])-float(self.service.clock())))
        if seconds<1:raise HubError('INVALID_STATE','Phiên đã hết hạn.',409)
        # Sign a fixed object version, so a later write cannot change shared content.
        params={'Bucket':bucket,'Key':key,
            'VersionId':metadata['VersionId'],'ResponseContentDisposition':'attachment'}
        url=self.client.generate_presigned_url('get_object',Params=params,ExpiresIn=seconds)
        return {'url':url,'expires_at':int(self.service.clock())+seconds,'key':key,
            'notice':'Ai có liên kết này có thể tải file cho tới thời hạn hoặc khi file bị thu hồi.'}

def sha(data):
    return hashlib.sha256(data).hexdigest()
