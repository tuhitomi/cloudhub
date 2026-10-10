"""T02 SDK processor. Two files per tick, immutable retained copies, scoped deletion."""
import json
import re
import time
import urllib.error
import urllib.request
from decimal import Decimal
from .aws_driver import AWSDriver
from .catalog import S3_CONFIG
from .errors import HubError
from .s3_files import assert_bucket, bucket_name, error_code, objects, read, sha, validate_file, validate_key
from .s3_template import s3_template

def canonical(value):
    def number(item):
        if isinstance(item, Decimal) and item == item.to_integral_value():
            return int(item)
        raise TypeError(type(item).__name__)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=number).encode()

class S3Driver(AWSDriver):
    def __init__(self, settings, clients=None, http_get=None, clock=time.time):
        self.settings, self.clock, self.http_get = settings, clock, http_get or self._http
        clients = clients or {}
        names = ["cloudformation", "s3", "cloudfront", "scheduler"]
        if any(name not in clients for name in names):
            import boto3
            from botocore.config import Config
            cfg = Config(connect_timeout=2, read_timeout=5, retries={"mode": "standard", "max_attempts": 2})
        for name in names:
            setattr(self, name, clients[name] if name in clients else boto3.client(name, config=cfg))

    @staticmethod
    def _http(url):
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read(16385)

    def create(self, env, op, recovery):
        if self.assert_stack(env):
            return
        self.cloudformation.create_stack(StackName=self.stack_name(env), TemplateBody=json.dumps(s3_template(env, self.settings)),
            ClientRequestToken=op["operation_id"], OnFailure="DO_NOTHING", RoleARN=self.settings["cf_role_arn"],
            Tags=[{"Key": "cloudhub:environment", "Value": env["environment_id"]}, {"Key": "cloudhub:owner", "Value": env["owner_id"]}])

    def created(self, env):
        stack = self.assert_stack(env)
        if not stack:
            raise RuntimeError("Stack disappeared during create")
        if stack["StackStatus"] in {"CREATE_FAILED", "ROLLBACK_COMPLETE", "ROLLBACK_FAILED"}:
            self.inventory(env)
            raise HubError("CREATE_FAILED", stack.get("StackStatusReason", stack["StackStatus"]), 409)
        if stack["StackStatus"] != "CREATE_COMPLETE":
            return False
        outputs = {v["OutputKey"]: v["OutputValue"] for v in stack["Outputs"]}
        env['bucket_name']=outputs['BucketName']
        if outputs.get('DistributionId'):env.update(distribution_id=outputs['DistributionId'],access_address=outputs['WebsiteUrl'])
        self.inventory(env)
        return True

    def inventory(self, env):
        resources = {(r["type"], r["id"]): r for r in env.get("resources", [])}
        if self.assert_stack(env):
            for page in self.cloudformation.get_paginator("list_stack_resources").paginate(StackName=self.stack_name(env)):
                for item in page["StackResourceSummaries"]:
                    if item.get("PhysicalResourceId"):
                        resources[(item["ResourceType"], item["PhysicalResourceId"])] = {"id": item["PhysicalResourceId"], "type": item["ResourceType"], "logical_id": item["LogicalResourceId"]}
                        if item["ResourceType"] == "AWS::CloudFront::Distribution":
                            env["distribution_id"] = item["PhysicalResourceId"]
        # Also discover the deterministic source when CreateStack response/metadata was lost.
        name = assert_bucket(self.s3, env, self.settings)
        if name:
            env["bucket_name"] = name
            resources[("AWS::S3::Bucket", name)] = {"id": name, "type": "AWS::S3::Bucket", "logical_id": "DataBucket"}
        env["resources"] = list(resources.values())
        return env["resources"]

    def _source(self, env):
        name = assert_bucket(self.s3, env, self.settings)
        if not name:
            raise HubError("SOURCE_MISSING", "Không tìm thấy bucket nguồn; từ chối xóa khi chưa có bản sao.", 409)
        return name

    def _prefix(self, rp):
        if not re.fullmatch(r"rp-[a-f0-9]{24}", rp["recovery_point"]):
            raise HubError("RESOURCE_SCOPE_MISMATCH", "ID bản sao không hợp lệ.", 409)
        return "recovery/" + rp["recovery_point"] + "/"

    def _bytes(self, bucket, key, limit=65536):
        response = self.s3.get_object(Bucket=bucket, Key=key, ExpectedBucketOwner=self.settings["account_id"])
        stream = response["Body"]
        try:
            data = stream.read(limit + 1)
        finally:
            stream.close()
        if len(data) > limit:
            raise HubError("BACKUP_CORRUPT", "Bản sao vượt kích thước đã duyệt.", 409)
        return data, response

    def _immutable(self, bucket, key, data, content_type, metadata):
        try:
            existing, _ = self._bytes(bucket, key)
        except Exception as exc:
            if error_code(exc) not in {"NoSuchKey", "404"}:
                raise
            self.s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type, Metadata=metadata,
                ServerSideEncryption="AES256", IfNoneMatch="*", ExpectedBucketOwner=self.settings["account_id"])
            existing, _ = self._bytes(bucket, key)
        if sha(existing) != sha(data):
            raise HubError("BACKUP_CORRUPT", "Nội dung sao lưu/khôi phục không khớp checksum.", 409)

    def ready(self, env, op, recovery):
        bucket = self._source(env)
        if op.get("action") in {"create", "restore"}:
            if recovery:
                if not self._restore(env, op, recovery, bucket):
                    return False
            else:
                for key, data, typ in [("index.html", b'<!doctype html><html lang="vi"><meta charset="utf-8"><title>CloudHub</title><h1>Website CloudHub</h1><p>Dang file HTML tu trang quan ly CloudHub.</p></html>', "text/html"),
                                       ("demo.txt", b"CloudHub T02 demo\n", "text/plain")]:
                    self._immutable(bucket, key, data, typ, {"cloudhub-operation": op["operation_id"]})
        marker = env["environment_id"].encode()
        self._immutable(bucket, ".cloudhub/ready.txt", marker, "text/plain", {"cloudhub-environment": env["environment_id"]})
        if env['template_id'] != 'T02':
            data,_=self._bytes(bucket,'.cloudhub/ready.txt')
            return data==marker
        distribution = self.cloudfront.get_distribution(Id=env["distribution_id"])["Distribution"]
        if distribution["Status"] != "Deployed" or not distribution["DistributionConfig"]["Enabled"]:
            return False
        # URL comes from the scoped distribution, never from a browser-supplied URL.
        domain = distribution["DomainName"]
        if not re.fullmatch(r"[a-z0-9]+\.cloudfront\.net", domain):
            raise HubError("RESOURCE_SCOPE_MISMATCH", "Domain CloudFront không hợp lệ.", 409)
        env["access_address"] = "https://" + domain
        try:
            status, data = self.http_get(env["access_address"] + "/.cloudhub/ready.txt")
            index_status, _ = self.http_get(env["access_address"] + "/index.html")
        except (urllib.error.URLError, TimeoutError):
            return False
        return status == 200 and data == marker and index_status == 200

    def _manifest(self, rp, owner):
        if rp["owner_id"] != owner or rp["template_id"] not in {'T02','T06','T07','T10'}:
            raise HubError("RESOURCE_SCOPE_MISMATCH", "Bản sao không thuộc người dùng/mẫu.", 409)
        data, _ = self._bytes(self.settings["s3_backup_bucket"], self._prefix(rp) + "manifest.json")
        if sha(data) != rp.get("manifest_sha256"):
            raise HubError("BACKUP_CORRUPT", "Manifest bản sao không khớp checksum.", 409)
        try:
            manifest = json.loads(data)
            entries = manifest["files"]
            if manifest["owner_id"] != owner or manifest["recovery_point"] != rp["recovery_point"] or manifest["environment_id"] != rp["environment_id"]:
                raise ValueError("scope")
            if len(entries) > S3_CONFIG["max_files"] or sum(v["size"] for v in entries) > S3_CONFIG["max_total_bytes"] or len({v["key"] for v in entries}) != len(entries):
                raise ValueError("limits")
            for item in entries:
                validate_key(item["key"])
                if type(item["size"]) is not int or not 0 <= item["size"] <= S3_CONFIG["max_file_bytes"] or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]):
                    raise ValueError("entry")
        except (ValueError, TypeError, KeyError, HubError):
            raise HubError("BACKUP_CORRUPT", "Manifest bản sao không hợp lệ.", 409)
        return entries

    def _restore(self, env, op, rp, bucket):
        entries = self._manifest(rp, env["owner_id"])
        cursor = int(op.get("restore_cursor", 0))
        for item in entries[cursor:cursor + 2]:
            data, _ = self._bytes(self.settings["s3_backup_bucket"], self._prefix(rp) + "data/" + item["key"], S3_CONFIG["max_file_bytes"])
            if sha(data) != item["sha256"] or len(data) != item["size"]:
                raise HubError("RESTORE_DATA_MISMATCH", "File bản sao không khớp manifest; giữ nguồn.", 409)
            self._immutable(bucket, item["key"], data, item["content_type"], {"cloudhub-operation": op["operation_id"]})
            cursor += 1
        op["restore_cursor"] = cursor
        if cursor < len(entries):
            return False
        verify = int(op.get("restore_verify_cursor", 0))
        for item in entries[verify:verify + 2]:
            data, _ = read(self.s3, bucket, item["key"], self.settings["account_id"])
            if sha(data) != item["sha256"]:
                raise HubError("RESTORE_DATA_MISMATCH", "Dữ liệu nguồn mới chưa khôi phục đúng.", 409)
            verify += 1
        op["restore_verify_cursor"] = verify
        if verify < len(entries):
            return False
        expected = [{"key": v["key"], "size": v["size"]} for v in entries]
        if objects(self.s3, bucket, self.settings) != sorted(expected, key=lambda v: v["key"]):
            raise HubError("RESTORE_DATA_MISMATCH", "Danh sách file khôi phục khác manifest.", 409)
        return True

    def write_file(self, env, op):
        bucket = self._source(env)
        data = validate_file(op["file"])
        key = op["file"]["key"]
        try:
            current = self.s3.head_object(Bucket=bucket, Key=key, ExpectedBucketOwner=self.settings["account_id"])
        except Exception as exc:
            if error_code(exc) not in {"404", "NoSuchKey"}:
                raise
            current = None
        if current and current.get("Metadata", {}).get("cloudhub-operation") == op["operation_id"]:
            actual, _ = read(self.s3, bucket, key, self.settings["account_id"])
            if sha(actual) != sha(data):
                raise HubError("FILE_WRITE_MISMATCH", "File đã ghi có checksum khác.", 409)
            return
        if current:
            actual, _ = read(self.s3, bucket, key, self.settings["account_id"])
            if actual == data and current.get("ContentType") == op["file"]["content_type"]:
                # Same bytes keep the same ETag; skip rather than create duplicate versions.
                op["file_sha256"] = sha(data)
                return
            if actual == data:
                raise HubError("FILE_TYPE_CONFLICT", "Nội dung không đổi nhưng định dạng khác; hãy dùng tên file mới.", 409)
        entries = [v for v in objects(self.s3, bucket, self.settings) if v["key"] != key]
        if len(entries) + 1 > S3_CONFIG["max_files"] or sum(v["size"] for v in entries) + len(data) > S3_CONFIG["max_total_bytes"]:
            raise HubError("FILE_QUOTA", "Đăng file vượt cấu hình được duyệt.", 409)
        condition = {"IfMatch": current["ETag"]} if current else {"IfNoneMatch": "*"}
        self.s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=op["file"]["content_type"],
            Metadata={"cloudhub-operation": op["operation_id"], "sha256": sha(data)}, ServerSideEncryption="AES256",
            ExpectedBucketOwner=self.settings["account_id"], **condition)
        actual, _ = read(self.s3, bucket, key, self.settings["account_id"])
        if sha(actual) != sha(data):
            raise HubError("FILE_WRITE_MISMATCH", "File sau đăng không khớp checksum.", 409)
        op["file_sha256"] = sha(data)

    def backup(self, env, op, rp):
        bucket = self._source(env)
        if "files" not in rp:
            rp["files"], rp["source"] = objects(self.s3, bucket, self.settings), bucket
            rp["backup_prefix"] = self._prefix(rp)
            rp["backup_bucket"] = self.settings["s3_backup_bucket"]
        cursor = int(op.get("backup_cursor", 0))
        for item in rp["files"][cursor:cursor + 2]:
            data, response = read(self.s3, bucket, item["key"], self.settings["account_id"], item.get("version_id"))
            item.update(version_id=response.get("VersionId"), sha256=sha(data), content_type=response.get("ContentType", "application/octet-stream"))
            if len(data) != item["size"]:
                raise HubError("SOURCE_CHANGED", "Nguồn thay đổi trong lúc khóa sao lưu; giữ nguyên nguồn.", 409)
            self._immutable(self.settings["s3_backup_bucket"], self._prefix(rp) + "data/" + item["key"], data,
                item["content_type"], {"cloudhub-recovery": rp["recovery_point"], "cloudhub-owner": env["owner_id"]})
            cursor += 1
        op["backup_cursor"] = cursor
        if cursor < len(rp["files"]):
            return False
        verify = int(op.get("backup_verify_cursor", 0))
        for item in rp["files"][verify:verify + 2]:
            current = self.s3.head_object(Bucket=bucket, Key=item["key"], ExpectedBucketOwner=self.settings["account_id"])
            copied, _ = self._bytes(self.settings["s3_backup_bucket"], self._prefix(rp) + "data/" + item["key"], S3_CONFIG["max_file_bytes"])
            if current.get("VersionId") != item["version_id"] or sha(copied) != item["sha256"]:
                raise HubError("BACKUP_DATA_MISMATCH", "Nguồn/bản sao thay đổi; từ chối thu hồi nguồn.", 409)
            verify += 1
        op["backup_verify_cursor"] = verify
        if verify < len(rp["files"]):
            return False
        if objects(self.s3, bucket, self.settings) != [{"key": v["key"], "size": v["size"]} for v in rp["files"]]:
            raise HubError("SOURCE_CHANGED", "Danh sách file nguồn thay đổi khi sao lưu.", 409)
        manifest = canonical({"recovery_point": rp["recovery_point"], "environment_id": env["environment_id"], "owner_id": env["owner_id"], "files": rp["files"]})
        self._immutable(self.settings["s3_backup_bucket"], self._prefix(rp) + "manifest.json", manifest, "application/json", {"cloudhub-recovery": rp["recovery_point"]})
        rp["manifest_sha256"] = sha(manifest)
        return True

    def resume(self, env):
        # No presigned writes; environment lock fences every writer throughout backup.
        return None

    def delete(self, env, op):
        if op.get("recovery_point") and "backup_cursor" not in op:
            raise HubError("BACKUP_REQUIRED", "Không có tiến trình sao lưu hoàn tất; giữ nguồn.", 409)
        bucket = assert_bucket(self.s3, env, self.settings)
        if bucket:
            page = self.s3.list_object_versions(Bucket=bucket, MaxKeys=500, ExpectedBucketOwner=self.settings["account_id"])
            entries = [{"Key": v["Key"], "VersionId": v["VersionId"]} for v in page.get("Versions", []) + page.get("DeleteMarkers", [])]
            if entries:
                result = self.s3.delete_objects(Bucket=bucket, Delete={"Objects": entries, "Quiet": True}, ExpectedBucketOwner=self.settings["account_id"])
                if result.get("Errors"):
                    raise HubError("DELETE_PARTIAL", "Một số phiên bản file chưa xóa được; cần quản trị kiểm tra.", 409)
                return False
        return super().delete(env, op)

    def audit(self, env, backups):
        report = {k: [] for k in ["deleted", "retained", "residual", "unknown", "billable"]}
        report["coverage"] = "Exact environment stack resources and deterministic bucket; retained S3 prefixes. Shared infrastructure excluded."
        resources = {(r["type"], r["id"]): r for r in env.get("resources", [])}
        name = bucket_name(env, self.settings)
        resources[("AWS::S3::Bucket", name)] = {"id": name, "type": "AWS::S3::Bucket"}
        for item in resources.values():
            typ = item["type"]
            try:
                if typ == "AWS::S3::Bucket":
                    exists = assert_bucket(self.s3, env, self.settings) is not None
                elif typ == "AWS::CloudFront::Distribution":
                    self.cloudfront.get_distribution(Id=item["id"])
                    exists = True
                elif typ == "AWS::CloudFront::OriginAccessControl":
                    self.cloudfront.get_origin_access_control(Id=item["id"])
                    exists = True
                elif typ == "AWS::S3::BucketPolicy":
                    self.s3.get_bucket_policy(Bucket=name, ExpectedBucketOwner=self.settings["account_id"])
                    exists = True
                else:
                    report["unknown"].append(item)
                    continue
                report["residual" if exists else "deleted"].append(item)
            except Exception as exc:
                if error_code(exc) in {"NoSuchBucket", "NoSuchDistribution", "NoSuchOriginAccessControl", "NoSuchBucketPolicy"}:
                    report["deleted"].append(item)
                else:
                    report["unknown"].append({**item, "error": error_code(exc) or type(exc).__name__})
        stack = self.assert_stack(env)
        if stack and stack["StackStatus"] != "DELETE_COMPLETE":
            report["residual"].append({"type": "AWS::CloudFormation::Stack", "id": self.stack_name(env)})
        for rp in backups:
            prefix = self._prefix(rp)
            page = self.s3.list_objects_v2(Bucket=self.settings["s3_backup_bucket"], Prefix=prefix, MaxKeys=1, ExpectedBucketOwner=self.settings["account_id"])
            if page.get("Contents"):
                item = {"type": "S3::RecoveryPrefix", "id": self.settings["s3_backup_bucket"] + "/" + prefix, "retain_until": rp["retain_until"]}
                policy = rp["status"] in {"AVAILABLE", "FAILED", "PENDING"} and (rp["retain_until"] > self.clock() or rp.get("restore_lock"))
                report["retained" if policy else "residual"].append(item)
        report["billable"] = [v for v in report["residual"] + report["retained"] if v["type"] in {"AWS::S3::Bucket", "AWS::CloudFront::Distribution", "S3::RecoveryPrefix"}]
        return report

    def expire_backup(self, rp):
        # Delete exact RP prefix only. Shared bucket and all other RPs survive.
        prefix = self._prefix(rp)
        page = self.s3.list_objects_v2(Bucket=self.settings["s3_backup_bucket"], Prefix=prefix, MaxKeys=100, ExpectedBucketOwner=self.settings["account_id"])
        entries = [{"Key": v["Key"]} for v in page.get("Contents", [])]
        if not entries:
            return True
        result = self.s3.delete_objects(Bucket=self.settings["s3_backup_bucket"], Delete={"Objects": entries, "Quiet": True}, ExpectedBucketOwner=self.settings["account_id"])
        if result.get("Errors"):
            raise HubError("BACKUP_EXPIRY_PARTIAL", "Bản sao hết hạn chưa dọn hết.", 409)
        return False
