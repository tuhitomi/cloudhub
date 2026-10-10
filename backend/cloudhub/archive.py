"""Small logical datasets: immutable S3 copy plus scoped integrity checks."""
import json,time
from .s3_driver import canonical
from .s3_files import error_code,sha
from .errors import HubError

class ArchiveMixin:
    def read_archive_body(self,response):
        body=response['Body']
        try:return body.read(65537)
        finally:body.close()
    def archive_key(self,rp):return 'recovery/'+rp['recovery_point']+'/dataset.json'
    def save_archive(self,env,rp,records):
        data=canonical({'owner_id':env['owner_id'],'environment_id':env['environment_id'],
            'template_id':env['template_id'],'recovery_point':rp['recovery_point'],'records':records})
        if len(data)>65536:raise HubError('DATA_QUOTA','Bản sao dữ liệu vượt 64 KiB; giữ nguồn.',409)
        params={'Bucket':self.settings['s3_backup_bucket'],'Key':self.archive_key(rp),'ExpectedBucketOwner':self.settings['account_id']}
        try:current=self.read_archive_body(self.s3.get_object(**params))
        except Exception as exc:
            if error_code(exc) not in {'NoSuchKey','404'}:raise
            self.s3.put_object(**params,Body=data,ServerSideEncryption='AES256',ContentType='application/json',IfNoneMatch='*',
                Metadata={'cloudhub-owner':env['owner_id'],'cloudhub-environment':env['environment_id'],'cloudhub-recovery':rp['recovery_point']})
            current=self.read_archive_body(self.s3.get_object(**params))
        if sha(current)!=sha(data):raise HubError('BACKUP_CORRUPT','Bản sao có nội dung khác; giữ nguồn.',409)
        rp.update(manifest_sha256=sha(data),data_sha256=sha(canonical(records)),record_count=len(records),source=env['data_source'])
    def load_archive(self,env,rp):
        response=self.s3.get_object(Bucket=self.settings['s3_backup_bucket'],Key=self.archive_key(rp),ExpectedBucketOwner=self.settings['account_id'])
        data=self.read_archive_body(response)
        if len(data)>65536 or sha(data)!=rp.get('manifest_sha256'):raise HubError('BACKUP_CORRUPT','Checksum bản sao sai.',409)
        value=json.loads(data)
        for key in ['owner_id','template_id']:
            if value[key]!=env[key] or value[key]!=rp[key]:raise HubError('RESOURCE_SCOPE_MISMATCH','Bản sao ngoài phạm vi.',409)
        if value['environment_id']!=rp['environment_id'] or value['recovery_point']!=rp['recovery_point']:
            raise HubError('RESOURCE_SCOPE_MISMATCH','Nguồn bản sao sai.',409)
        return value['records']
    def audit_archive(self,env,backups,report):
        for rp in backups:
            key=self.archive_key(rp)
            try:
                metadata=self.s3.head_object(Bucket=self.settings['s3_backup_bucket'],Key=key,ExpectedBucketOwner=self.settings['account_id'])['Metadata']
            except Exception as exc:
                if error_code(exc) in {'NoSuchKey','404'}:continue
                report['unknown'].append({'type':'S3::BackupObject','id':key,'error':error_code(exc)});continue
            if metadata.get('cloudhub-environment')!=env['environment_id'] or metadata.get('cloudhub-owner')!=env['owner_id']:
                report['unknown'].append({'type':'S3::BackupObject','id':key,'error':'scope'});continue
            retain=rp['status']=='AVAILABLE' and (rp['retain_until']>time.time() or rp.get('restore_lock'))
            report['retained' if retain else 'residual'].append({'type':'S3::BackupObject','id':key,'retain_until':rp['retain_until']})
    def expire_archive(self,rp):
        key=self.archive_key(rp);params={'Bucket':self.settings['s3_backup_bucket'],'Key':key,'ExpectedBucketOwner':self.settings['account_id']}
        try:metadata=self.s3.head_object(**params)['Metadata']
        except Exception as exc:
            if error_code(exc) in {'NoSuchKey','404'}:return True
            raise
        if any(metadata.get('cloudhub-'+k)!=rp[k+'_id'] for k in ['owner','environment']) or metadata.get('cloudhub-recovery')!=rp['recovery_point']:
            raise HubError('RESOURCE_SCOPE_MISMATCH','Bản sao retention ngoài phạm vi.',409)
        self.s3.delete_object(**params);return True
