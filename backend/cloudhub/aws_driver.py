"""SDK operations are bounded; Step Functions owns waiting, retries and errors."""
import json
from datetime import datetime, timezone
from .templates import linux_template
from .errors import HubError

MANIFEST = """set -eu
mountpoint -q /data
mkdir -p /data/.cloudhub
find /data -type f ! -path '/data/.cloudhub/*' -exec sha256sum {} + | LC_ALL=C sort > /data/.cloudhub/manifest.sha256
sync
sha256sum /data/.cloudhub/manifest.sha256
"""
VERIFY = """set -eu
mountpoint -q /data
test -f /data/.cloudhub/ready
test -f /data/demo.txt
"""
RESTORE_VERIFY = VERIFY + """test -f /data/.cloudhub/manifest.sha256
sha256sum -c /data/.cloudhub/manifest.sha256 >/dev/null
find /data -type f ! -path '/data/.cloudhub/*' -exec sha256sum {} + | LC_ALL=C sort > /tmp/cloudhub-verify
cmp /tmp/cloudhub-verify /data/.cloudhub/manifest.sha256
sha256sum /data/.cloudhub/manifest.sha256
"""

def code(exc):
    return getattr(exc, "response", {}).get("Error", {}).get("Code", "")

class AWSDriver:
    def __init__(self, settings, clients=None):
        import boto3
        from botocore.config import Config
        self.settings = settings
        clients = clients or {}
        cfg = Config(connect_timeout=2, read_timeout=10, retries={"mode": "standard", "max_attempts": 2})
        for name in ["cloudformation", "ec2", "ssm", "scheduler"]:
            setattr(self, name, clients.get(name) or boto3.client(name, config=cfg))
        self.aws_backup = clients.get("backup") or boto3.client("backup", config=cfg)

    def stack_name(self, env):
        return "cloudhub-" + env["environment_id"]

    def assert_stack(self, env):
        try:
            stacks = self.cloudformation.describe_stacks(StackName=self.stack_name(env))["Stacks"]
        except Exception as exc:
            if code(exc) == "ValidationError" and "does not exist" in str(exc):
                return None
            raise
        stack = stacks[0]
        tags = {t["Key"]: t["Value"] for t in stack.get("Tags", [])}
        if tags.get("cloudhub:environment") != env["environment_id"] or tags.get("cloudhub:owner") != env["owner_id"]:
            raise HubError("RESOURCE_SCOPE_MISMATCH", "Stack không thuộc môi trường; từ chối thao tác.", 409)
        return stack

    def create(self, env, op, recovery):
        existing = self.assert_stack(env)
        if existing:
            return
        template = linux_template(env, self.settings, recovery.get("snapshot_id") if recovery else None)
        self.cloudformation.create_stack(StackName=self.stack_name(env), TemplateBody=json.dumps(template),
            ClientRequestToken=op["operation_id"], OnFailure="DO_NOTHING", RoleARN=self.settings["cf_role_arn"],
            Tags=[{"Key": "cloudhub:environment", "Value": env["environment_id"]},
                  {"Key": "cloudhub:owner", "Value": env["owner_id"]}])

    def created(self, env):
        stack = self.assert_stack(env)
        if not stack:
            raise RuntimeError("Stack disappeared during create")
        status = stack["StackStatus"]
        if status in {"CREATE_FAILED", "ROLLBACK_COMPLETE", "ROLLBACK_FAILED"}:
            # Discover partial resources before failing, preserving data-aware cleanup.
            self.inventory(env)
            raise HubError("CREATE_FAILED", stack.get("StackStatusReason", status), 409)
        if status != "CREATE_COMPLETE":
            return False
        outputs = {v["OutputKey"]: v["OutputValue"] for v in stack["Outputs"]}
        env.update(instance_id=outputs["InstanceId"], data_volume_id=outputs["DataVolumeId"],
                   key_pair_id=outputs["KeyPairId"], public_ip=outputs["PublicIp"],
                   access_address="http://" + outputs["PublicIp"] if env["template_id"] == "T05" else
                   "ssh://ec2-user@" + outputs["PublicIp"])
        self.inventory(env)
        return True

    def inventory(self, env):
        rows = []
        try:
            pages = self.cloudformation.get_paginator("list_stack_resources").paginate(StackName=self.stack_name(env))
            for page in pages:
                for r in page["StackResourceSummaries"]:
                    if "PhysicalResourceId" in r:
                        rows.append({"type": r["ResourceType"], "id": r["PhysicalResourceId"], "logical_id": r["LogicalResourceId"]})
        except Exception as exc:
            if not (code(exc) == "ValidationError" and "does not exist" in str(exc)):
                raise
        instance = next((r["id"] for r in rows if r["type"] == "AWS::EC2::Instance"), None)
        volume = next((r["id"] for r in rows if r["logical_id"] == "DataVolume"), None)
        if instance:
            env["instance_id"] = instance
            r = self.ec2.describe_instances(InstanceIds=[instance])["Reservations"]
            for reservation in r:
                for inst in reservation["Instances"]:
                    for interface in inst.get("NetworkInterfaces", []):
                        rows.append({"type": "AWS::EC2::NetworkInterface", "id": interface["NetworkInterfaceId"], "logical_id": "PrimaryInterface"})
                    for mapping in inst.get("BlockDeviceMappings", []):
                        vid = mapping.get("Ebs", {}).get("VolumeId")
                        if vid and vid != volume:
                            rows.append({"type": "AWS::EC2::Volume", "id": vid, "logical_id": "RootVolume"})
        if volume:
            env["data_volume_id"] = volume
        key = next((r["id"] for r in rows if r["type"] == "AWS::EC2::KeyPair"), None)
        if key:
            pairs = self.ec2.describe_key_pairs(KeyNames=[key])["KeyPairs"]
            if pairs:
                env["key_pair_id"] = pairs[0]["KeyPairId"]
                rows.append({"type": "AWS::SSM::Parameter", "id": "/ec2/keypair/" + env["key_pair_id"], "logical_id": "PrivateKeyParameter"})
        if rows:
            env["resources"] = rows

    def command(self, env, op, script, field):
        if field not in op:
            r = self.ssm.send_command(InstanceIds=[env["instance_id"]], DocumentName="AWS-RunShellScript",
                    Parameters={"commands": [script]}, TimeoutSeconds=120, Comment=op["operation_id"])
            op[field] = r["Command"]["CommandId"]
            return None
        try:
            r = self.ssm.get_command_invocation(CommandId=op[field], InstanceId=env["instance_id"])
        except Exception as exc:
            if code(exc) == "InvocationDoesNotExist":
                return None
            raise
        if r["Status"] in {"Pending", "InProgress", "Delayed"}:
            return None
        if r["Status"] != "Success":
            raise HubError("DATA_CHECK_FAILED", "SSM command failed: " + r["Status"], 409)
        return r.get("StandardOutputContent", "").strip()

    def ready(self, env, op, recovery):
        reservations = self.ec2.describe_instance_status(InstanceIds=[env["instance_id"]], IncludeAllInstances=True)["InstanceStatuses"]
        if not reservations or reservations[0]["InstanceStatus"]["Status"] != "ok" or reservations[0]["SystemStatus"]["Status"] != "ok":
            return False
        info = self.ssm.describe_instance_information(Filters=[{"Key": "InstanceIds", "Values": [env["instance_id"]]}])["InstanceInformationList"]
        if not info or info[0]["PingStatus"] != "Online":
            return False
        instance = self.ec2.describe_instances(InstanceIds=[env["instance_id"]])["Reservations"][0]["Instances"][0]
        if not instance.get("PublicIpAddress"):
            return False
        env["public_ip"] = instance["PublicIpAddress"]
        if env["template_id"] == "T05":
            env["access_address"] = "http://" + instance["PublicIpAddress"]
        else:
            env["access_address"] = "ssh://ec2-user@" + instance["PublicIpAddress"]
        script = RESTORE_VERIFY if recovery else VERIFY
        if env["template_id"] == "T05":
            script += "curl --fail --silent http://127.0.0.1/ >/dev/null\n"
        try:
            result = self.command(env, op, script, "ready_command")
        except HubError as exc:
            if exc.code != "DATA_CHECK_FAILED":
                raise
            # SSM can be online before cloud-init mounts /data or installs nginx.
            # Corrupt restores remain unready and time out without declaring success.
            op.pop("ready_command", None)
            return False
        if result is None:
            return False
        if recovery and result.split()[0] != recovery["manifest_sha256"]:
            raise HubError("RESTORE_CHECKSUM_MISMATCH", "Dữ liệu khôi phục khác bản sao.", 409)
        return True

    def backup(self, env, op, rp):
        if "manifest_sha256" not in rp:
            result = self.command(env, op, MANIFEST, "manifest_command")
            if result is None:
                return False
            rp["manifest_sha256"] = result.split()[0]
        self.assert_instance(env)
        reservations = self.ec2.describe_instances(InstanceIds=[env["instance_id"]])["Reservations"]
        state = reservations[0]["Instances"][0]["State"]["Name"]
        if state == "running":
            self.ec2.stop_instances(InstanceIds=[env["instance_id"]])
            return False
        if state != "stopped":
            return False
        if not rp.get("backup_job_id"):
            r = self.aws_backup.start_backup_job(BackupVaultName=self.settings["backup_vault"],
                ResourceArn="arn:aws:ec2:" + self.settings["region"] + ":" + self.settings["account_id"] + ":volume/" + env["data_volume_id"],
                IamRoleArn=self.settings["backup_role"], IdempotencyToken=op["operation_id"]+('-r'+str(op['generation']) if op.get('generation') else ''),
                StartWindowMinutes=60, CompleteWindowMinutes=120,
                RecoveryPointTags={"cloudhub:environment": env["environment_id"],
                    "cloudhub:recovery": rp["recovery_point"], "cloudhub:owner": env["owner_id"]})
            rp["backup_job_id"] = r["BackupJobId"]
            return False
        job = self.aws_backup.describe_backup_job(BackupJobId=rp["backup_job_id"])
        if job["State"] in {"ABORTED", "FAILED", "EXPIRED", "PARTIAL"}:
            raise HubError("BACKUP_FAILED", "AWS Backup thất bại; giữ nguồn dữ liệu.", 409)
        if job["State"] != "COMPLETED":
            return False
        arn = job["RecoveryPointArn"]
        if ":snapshot/snap-" not in arn:
            raise HubError("BACKUP_SCOPE_MISMATCH", "Recovery point không phải EBS snapshot.", 409)
        rp["aws_recovery_point_arn"] = arn
        rp["snapshot_id"] = arn.rsplit("/", 1)[1]
        return True

    def assert_instance(self, env):
        self.assert_stack(env)
        inst = self.ec2.describe_instances(InstanceIds=[env["instance_id"]])["Reservations"][0]["Instances"][0]
        tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
        if tags.get("cloudhub:environment") != env["environment_id"] or tags.get("cloudhub:owner") != env["owner_id"]:
            raise HubError("RESOURCE_SCOPE_MISMATCH", "Instance ngoài phạm vi môi trường.", 409)

    def resume(self, env):
        self.assert_instance(env)
        self.ec2.start_instances(InstanceIds=[env["instance_id"]])

    def delete(self, env, op):
        stack = self.assert_stack(env)
        if not stack:
            return True
        status = stack["StackStatus"]
        if status == "DELETE_COMPLETE":
            return True
        if status == "DELETE_FAILED":
            raise HubError("DELETE_FAILED", "CloudFormation thu hồi thất bại; cần quản trị.", 409)
        if status != "DELETE_IN_PROGRESS":
            self.inventory(env)
            self.cloudformation.delete_stack(StackName=self.stack_name(env), ClientRequestToken=op["operation_id"], RoleARN=self.settings["cf_role_arn"])
        return False

    def audit(self, env, backups):
        report = {"deleted": [], "retained": [], "residual": [], "unknown": [], "billable": [],
                  "coverage": "EC2, EBS (including root), ENI, security group, SSH key pair/private-key parameter, EBS snapshots, CloudFormation; T01/T05"}
        found = {}
        filters = [{"Name": "tag:cloudhub:environment", "Values": [env["environment_id"]]}]
        for pair in self.ec2.describe_key_pairs(Filters=filters)["KeyPairs"]:
            found[pair["KeyName"]] = {"type": "AWS::EC2::KeyPair", "id": pair["KeyName"]}
        for api, key, typ, id_key in [("describe_instances", "Reservations", "AWS::EC2::Instance", "InstanceId"),
                    ("describe_volumes", "Volumes", "AWS::EC2::Volume", "VolumeId"),
                    ("describe_network_interfaces", "NetworkInterfaces", "AWS::EC2::NetworkInterface", "NetworkInterfaceId"),
                    ("describe_security_groups", "SecurityGroups", "AWS::EC2::SecurityGroup", "GroupId")]:
            for page in self.ec2.get_paginator(api).paginate(Filters=filters):
                entries = page[key]
                if key == "Reservations":
                    entries = [i for r in entries for i in r["Instances"] if i["State"]["Name"] != "terminated"]
                for item in entries:
                    found[item[id_key]] = {"type": typ, "id": item[id_key]}
        # Root EBS and ENIs may not inherit instance tags: reconcile saved IDs with point lookups too.
        for resource in env.get("resources", []):
            typ, resource_id = resource["type"], resource["id"]
            lookup = {"AWS::EC2::Volume": ("describe_volumes", "VolumeIds", "Volumes", "InvalidVolume.NotFound"),
                      "AWS::EC2::NetworkInterface": ("describe_network_interfaces", "NetworkInterfaceIds", "NetworkInterfaces", "InvalidNetworkInterfaceID.NotFound"),
                      "AWS::EC2::SecurityGroup": ("describe_security_groups", "GroupIds", "SecurityGroups", "InvalidGroup.NotFound"),
                      "AWS::EC2::KeyPair": ("describe_key_pairs", "KeyNames", "KeyPairs", "InvalidKeyPair.NotFound"),
                      "AWS::EC2::Instance": ("describe_instances", "InstanceIds", "Reservations", "InvalidInstanceID.NotFound")}
            if typ in lookup:
                method, parameter, response_key, not_found = lookup[typ]
                try:
                    r = getattr(self.ec2, method)(**{parameter: [resource_id]})[response_key]
                    if typ == "AWS::EC2::Instance":
                        r = [i for reservation in r for i in reservation["Instances"] if i["State"]["Name"] != "terminated"]
                    if r:
                        found[resource_id] = resource
                except Exception as exc:
                    if code(exc) != not_found:
                        raise
            if typ == "AWS::SSM::Parameter":
                try:
                    self.ssm.get_parameter(Name=resource_id, WithDecryption=False)
                    found[resource_id] = resource
                except Exception as exc:
                    if code(exc) != "ParameterNotFound":
                        raise
            if typ == "AWS::EC2::VolumeAttachment":
                continue
            if resource_id in found:
                continue
            if typ in set(lookup) | {"AWS::SSM::Parameter"}:
                report["deleted"].append(resource)
        now = int(datetime.now(timezone.utc).timestamp())
        retained = {b.get("snapshot_id"): b for b in backups if b.get("snapshot_id") and b["status"] == "AVAILABLE"
                    and (b["retain_until"] > now or b.get("restore_lock"))}
        for page in self.ec2.get_paginator("describe_snapshots").paginate(OwnerIds=["self"], Filters=filters):
            for snap in page["Snapshots"]:
                item = {"type": "AWS::EC2::Snapshot", "id": snap["SnapshotId"]}
                if snap["SnapshotId"] in retained:
                    item["retain_until"] = retained[snap["SnapshotId"]]["retain_until"]
                    report["retained"].append(item)
                else:
                    found[snap["SnapshotId"]] = item
        report["residual"] = list(found.values())
        stack = self.assert_stack(env)
        if stack and stack["StackStatus"] != "DELETE_COMPLETE":
            report["residual"].append({"type": "AWS::CloudFormation::Stack", "id": self.stack_name(env)})
        report["billable"] = [r for r in report["retained"] + report["residual"] if r["type"] in {"AWS::EC2::Instance", "AWS::EC2::Volume", "AWS::EC2::Snapshot", "AWS::EC2::NetworkInterface"}]
        return report

    def schedule(self, env):
        stamp = datetime.fromtimestamp(int(env["deadline"]), timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        name = env["environment_id"] + "-" + str(env["deadline"])
        try:
            self.scheduler.create_schedule(Name=name, GroupName=self.settings["schedule_group"],
                ScheduleExpression="at(" + stamp + ")", ScheduleExpressionTimezone="UTC",
                FlexibleTimeWindow={"Mode": "OFF"}, ActionAfterCompletion="DELETE",
                ClientToken=name, Target={"Arn": self.settings["expiry_arn"], "RoleArn": self.settings["scheduler_role"],
                    "Input": json.dumps({"environment_id": env["environment_id"]}),
                    "RetryPolicy": {"MaximumEventAgeInSeconds": 3600, "MaximumRetryAttempts": 3},
                    "DeadLetterConfig": {"Arn": self.settings["dlq_arn"]}})
        except Exception as exc:
            if code(exc) != "ConflictException":
                raise

    def expire_backup(self, rp):
        """Missing local metadata is not evidence that AWS has no backup."""
        arn = rp.get('aws_recovery_point_arn')
        terminal_without_point = False
        if not arn and rp.get('backup_job_id'):
            job = self.aws_backup.describe_backup_job(BackupJobId=rp['backup_job_id'])
            source = rp.get('source')
            if source and job.get('ResourceArn') != self._volume_arn(source):
                raise HubError('RESOURCE_SCOPE_MISMATCH', 'Backup job thuộc nguồn khác.', 409)
            if job['State'] not in {'COMPLETED', 'FAILED', 'ABORTED', 'EXPIRED', 'PARTIAL'}:
                return False
            arn = job.get('RecoveryPointArn')
            terminal_without_point = job['State'] in {'FAILED', 'ABORTED', 'EXPIRED'} and not arn
        if not arn and rp.get('snapshot_id'):
            arn = self._snapshot_prefix() + rp['snapshot_id']
        targets = set(rp.get('cleanup_arns', []))
        if arn:
            targets.add(arn)
        if not targets:
            # Lost StartBackupJob responses have no job ID. Discover only points
            # whose three ownership tags match; never delete by volume alone.
            args = {'BackupVaultName': self.settings['backup_vault']}
            while True:
                page = self.aws_backup.list_recovery_points_by_backup_vault(**args)
                for point in page['RecoveryPoints']:
                    candidate = point['RecoveryPointArn']
                    if not candidate.startswith(self._snapshot_prefix()):
                        continue
                    point_tags = self.aws_backup.list_tags(ResourceArn=candidate)['Tags']
                    if self._recovery_tags_match(point_tags, rp):
                        targets.add(candidate)
                if not page.get('NextToken'):
                    break
                args['NextToken'] = page['NextToken']
            if not targets:
                snapshots = self.ec2.describe_snapshots(
                    OwnerIds=[self.settings['account_id']],
                    Filters=[{'Name': 'tag:cloudhub:' + key, 'Values': [rp[field]]}
                             for key, field in [('recovery', 'recovery_point'), ('owner', 'owner_id'),
                                                ('environment', 'environment_id')]])['Snapshots']
                for snapshot in snapshots:
                    tags = {tag['Key']: tag['Value'] for tag in snapshot.get('Tags', [])}
                    if self._recovery_tags_match(tags, rp):
                        targets.add(self._snapshot_prefix() + snapshot['SnapshotId'])
            if not targets:
                if terminal_without_point:
                    return True
                raise HubError('RECOVERY_UNCONFIRMED',
                               'Chưa đối chiếu được backup job hoặc recovery point; không đánh dấu đã xóa.', 409)
        rp['cleanup_arns'] = sorted(targets)
        absent = True
        for target in sorted(targets):
            # Do not short-circuit: each owned point needs its deletion advanced.
            absent = self._expire_snapshot(target, rp) and absent
        return absent

    def _snapshot_prefix(self):
        return 'arn:aws:ec2:' + self.settings['region'] + ':' + self.settings['account_id'] + ':snapshot/'

    def _volume_arn(self, volume):
        return 'arn:aws:ec2:' + self.settings['region'] + ':' + self.settings['account_id'] + ':volume/' + volume

    @staticmethod
    def _recovery_tags_match(tags, rp):
        return all(tags.get('cloudhub:' + key) == rp[field] for key, field in
                   [('recovery', 'recovery_point'), ('owner', 'owner_id'), ('environment', 'environment_id')])

    def _expire_snapshot(self, arn, rp):
        if not arn.startswith(self._snapshot_prefix() + 'snap-'):
            raise HubError('RESOURCE_SCOPE_MISMATCH', 'Recovery point ngoài tài khoản/region hoặc không phải EBS.', 409)
        point = None
        try:
            point = self.aws_backup.describe_recovery_point(
                BackupVaultName=self.settings['backup_vault'], RecoveryPointArn=arn)
            point_tags = self.aws_backup.list_tags(ResourceArn=arn)['Tags']
            if not self._recovery_tags_match(point_tags, rp):
                raise HubError('RESOURCE_SCOPE_MISMATCH', 'Recovery point ngoài phạm vi chính sách.', 409)
            if rp.get('source') and point.get('ResourceArn') != self._volume_arn(rp['source']):
                raise HubError('RESOURCE_SCOPE_MISMATCH', 'Recovery point thuộc volume khác.', 409)
        except Exception as exc:
            if code(exc) != 'ResourceNotFoundException':
                raise
            if point is not None:
                return False  # Deletion raced ListTags; check again next poll.
        snap = None
        try:
            snapshots = self.ec2.describe_snapshots(SnapshotIds=[arn.rsplit('/', 1)[1]])['Snapshots']
            snap = snapshots[0] if snapshots else None
        except Exception as exc:
            if code(exc) != 'InvalidSnapshot.NotFound':
                raise
        if snap and not self._recovery_tags_match({t['Key']: t['Value'] for t in snap.get('Tags', [])}, rp):
            raise HubError('RESOURCE_SCOPE_MISMATCH', 'Snapshot ngoài phạm vi chính sách.', 409)
        if point is None:
            if snap is None:
                return True
            raise HubError('RECOVERY_UNCONFIRMED', 'Snapshot vẫn còn nhưng vault không có recovery point; cần xử lý trước khi báo đã xóa.', 409)
        if point.get('Status') != 'DELETING':
            self.aws_backup.delete_recovery_point(BackupVaultName=self.settings['backup_vault'], RecoveryPointArn=arn)
        return False
