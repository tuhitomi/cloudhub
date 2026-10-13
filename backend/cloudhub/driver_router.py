"""Select a processor from persisted metadata, never from request resource IDs."""
from .aws_driver import AWSDriver
from .s3_driver import S3Driver
from .rds_driver import RDSDriver
from .data_driver import DataDriver
from .ecs_driver import ECSDriver
from .efs_driver import EFSDriver
from .redshift_driver import RedshiftDriver
from .errors import HubError

class DriverRouter:
    def __init__(self, settings):
        self.settings, self.drivers = settings, {}

    def driver(self, row):
        classes={'T01':AWSDriver,'T02':S3Driver,'T03':RDSDriver,'T04':RDSDriver,'T05':AWSDriver,'T06':ECSDriver,'T07':S3Driver,'T08':EFSDriver,'T09':DataDriver,'T10':RedshiftDriver}
        kind=classes.get(row['template_id'])
        if not kind:
            raise HubError("TEMPLATE_UNAVAILABLE", "Không có bộ xử lý mẫu.", 409)
        if kind not in self.drivers:
            self.drivers[kind]=kind(self.settings)
        return self.drivers[kind]

    def __getattr__(self, name):
        if name not in {"create", "created", "ready", "schedule", "cancel_schedule", "pause", "pause_audit", "backup", "resume", "delete", "inventory", "audit", "expire_backup", "write_file",'use_data'}:
            raise AttributeError(name)
        return lambda row, *args: getattr(self.driver(row), name)(row, *args)

    def expire_backup(self, row):
        if not self.driver(row).expire_backup(row):return False
        # Native snapshot processors do not remove the portable export objects.
        import boto3,re
        if not re.fullmatch(r'rp-[a-f0-9]{24}',row['recovery_point']):
            raise HubError('RESOURCE_SCOPE_MISMATCH','ID bản sao không hợp lệ.',409)
        s3=boto3.client('s3');bucket=self.settings['s3_backup_bucket']
        page=s3.list_objects_v2(Bucket=bucket,Prefix='recovery/'+row['recovery_point']+'/exports/',MaxKeys=100,ExpectedBucketOwner=self.settings['account_id'])
        entries=[]
        for item in page.get('Contents',[]):
            meta=s3.head_object(Bucket=bucket,Key=item['Key'],ExpectedBucketOwner=self.settings['account_id']).get('Metadata',{})
            if any(meta.get('cloudhub-'+key)!=row[key+'_id'] for key in ['owner','environment']) or meta.get('cloudhub-recovery')!=row['recovery_point']:
                raise HubError('RESOURCE_SCOPE_MISMATCH','File xuất ngoài phạm vi bản sao.',409)
            entries.append({'Key':item['Key']})
        if not entries:return True
        result=s3.delete_objects(Bucket=bucket,Delete={'Objects':entries,'Quiet':True},ExpectedBucketOwner=self.settings['account_id'])
        if result.get('Errors'):raise HubError('BACKUP_DELETE_PARTIAL','Một số file xuất chưa xóa được.',409)
        return False
