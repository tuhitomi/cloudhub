"""T08 freezes both mounted clients, uses AWS Backup, restores to a new tagged FS."""
import base64,json,time
from .stack_driver import StackDriver
from .efs_template import efs_template
from .aws_driver import code
from .errors import HubError

class EFSDriver(StackDriver):
    def build_template(self,env,recovery):return efs_template(env,self.settings,recovery)
    def apply_outputs(self,env):
        env.update(file_system_id=env['outputs']['FileSystemId'],data_source=env['outputs']['FileSystemId'],
            node_ids=[env['outputs']['Node1Id'],env['outputs']['Node2Id']],access_address='Hai máy Linux: quản lý dữ liệu qua tác vụ CloudHub')
    def fs(self,env):
        identifier=env.get('file_system_id') or next((v['id'] for v in env['resources'] if v['type']=='AWS::EFS::FileSystem'),None)
        if not identifier:raise HubError('SOURCE_MISSING','Không có EFS nguồn.',409)
        detail=self.efs.describe_file_systems(FileSystemId=identifier)['FileSystems'][0]
        tags={v['Key']:v['Value'] for v in self.efs.list_tags_for_resource(ResourceId=identifier)['Tags']}
        if tags.get('cloudhub:environment')!=env['environment_id'] or tags.get('cloudhub:owner')!=env['owner_id']:
            raise HubError('RESOURCE_SCOPE_MISMATCH','EFS ngoài phạm vi.',409)
        env.update(file_system_id=identifier,data_source=identifier,data_source_arn=detail['FileSystemArn']);return detail
    def node_command(self,env,op,node,script,field):
        env['instance_id']=node
        return self.command(env,op,script,field+'_'+node)
    def nodes(self,env):
        nodes=env.get('node_ids') or [v['id'] for v in env.get('resources',[]) if v['type']=='AWS::EC2::Instance']
        if len(nodes)!=2:raise HubError('SOURCE_MISSING','Cần đúng hai máy Linux EFS.',409)
        return nodes
    def ready(self,env,op,recovery):
        detail=self.fs(env)
        if detail['LifeCycleState']!='available':return False
        for node in self.nodes(env):
            online=self.ssm.describe_instance_information(Filters=[{'Key':'InstanceIds','Values':[node]}])['InstanceInformationList']
            if not online or online[0]['PingStatus']!='Online':return False
        if recovery and not op.get('efs_restore_done'):
            if not op.get('efs_restore_job'):
                response=self.awsbackup.start_restore_job(RecoveryPointArn=recovery['recovery_point_arn'],
                    IamRoleArn=self.settings['backup_restore_role'],IdempotencyToken=op['operation_id'],ResourceType='EFS',
                    CopySourceTagsToRestoredResource=False,Metadata={'file-system-id':env['file_system_id'],'newFileSystem':'false',
                        'Encrypted':'true','PerformanceMode':'generalPurpose','CreationToken':op['operation_id']})
                op['efs_restore_job']=response['RestoreJobId'];return False
            job=self.awsbackup.describe_restore_job(RestoreJobId=op['efs_restore_job'])
            if job['Status'] in {'FAILED','ABORTED'}:raise HubError('RESTORE_FAILED',job.get('StatusMessage','EFS restore failed'),409)
            if job['Status']!='COMPLETED':return False
            script="""set -eu
mountpoint -q /shared
python3 - <<'PY'
import pathlib,shutil
root=pathlib.Path('/shared');candidates=list(root.glob('aws-backup-restore_*'))
if len(candidates)!=1:raise ValueError('Expected exactly one recovery directory')
source=candidates[0]/'records'
if not source.is_dir() or source.is_symlink():raise ValueError('Invalid recovery directory')
target=root/'records'
for path in source.iterdir():
 if path.is_symlink() or not path.is_file():raise ValueError('Unexpected file')
 shutil.copyfile(path,target/path.name)
PY
python3 /opt/cloudhub/records.py
"""
            result=self.node_command(env,op,self.nodes(env)[0],script,'efs_restore_copy')
            if result is None:return False
            if json.loads(result)['sha256']!=recovery['manifest_sha256']:raise HubError('RESTORE_DATA_MISMATCH','EFS restore checksum khác nguồn.',409)
            op['efs_restore_done']=True
        checks=[]
        for node in self.nodes(env):
            script='set -eu\nmountpoint -q /shared\nfindmnt -n -o OPTIONS /shared | grep -q rw\npython3 /opt/cloudhub/records.py\n'
            try:result=self.node_command(env,op,node,script,'efs_ready')
            except HubError as exc:
                if exc.code!='DATA_CHECK_FAILED':raise
                op.pop('efs_ready_'+node,None);return False
            if result is None:return False
            checks.append(json.loads(result)['sha256'])
        if len(set(checks))!=1 or recovery and checks[0]!=recovery['manifest_sha256']:raise HubError('RESTORE_DATA_MISMATCH','Hai máy không thấy cùng dữ liệu EFS.',409)
        return True
    @property
    def awsbackup(self):
        if 'backup' not in self.clients:
            import boto3
            from botocore.config import Config
            self.clients['backup']=boto3.client('backup',config=Config(connect_timeout=2,read_timeout=5,retries={'total_max_attempts':2}))
        return self.clients['backup']
    def backup(self,env,op,rp):
        detail=self.fs(env)
        for node in self.nodes(env):
            result=self.node_command(env,op,node,'set -eu\nmountpoint -q /shared\nsync\nmount -o remount,ro /shared\n','efs_freeze')
            if result is None:return False
        if not rp.get('manifest_sha256'):
            result=self.node_command(env,op,self.nodes(env)[0],'python3 /opt/cloudhub/records.py','efs_manifest')
            if result is None:return False
            value=json.loads(result);rp.update(manifest_sha256=value['sha256'],source=detail['FileSystemArn'],record_count=len(value['items']))
        if not op.get('backup_job_id'):
            response=self.awsbackup.start_backup_job(BackupVaultName=self.settings['backup_vault'],ResourceArn=detail['FileSystemArn'],
                IamRoleArn=self.settings['backup_role'],IdempotencyToken=op['operation_id']+('-r'+str(op['generation']) if op.get('generation') else ''),Lifecycle={'DeleteAfterDays':7},
                RecoveryPointTags={'cloudhub:owner':env['owner_id'],'cloudhub:environment':env['environment_id'],'cloudhub:recovery':rp['recovery_point']})
            op['backup_job_id']=response['BackupJobId']
            rp['backup_job_id']=response['BackupJobId']
            if response.get('RecoveryPointArn'):rp['recovery_point_arn']=response['RecoveryPointArn']
            return False
        job=self.awsbackup.describe_backup_job(BackupJobId=op['backup_job_id']);rp['backup_job_id']=op['backup_job_id']
        if job['State'] in {'FAILED','ABORTED','EXPIRED'}:raise HubError('BACKUP_FAILED',job.get('StatusMessage','EFS backup failed; source retained'),409)
        if job['State']!='COMPLETED':return False
        rp['recovery_point_arn']=job['RecoveryPointArn'];return True
    def use_data(self,env,op):
        self.fs(env);nodes=self.nodes(env)
        script='set -eu\nmountpoint -q /shared\npython3 /opt/cloudhub/records.py'
        if op['action']=='put_record':script+=' '+base64.b64encode(json.dumps(op['data']).encode()).decode()
        result=self.node_command(env,op,nodes[0],script+'\n','efs_write')
        if result is None:return False
        second=self.node_command(env,op,nodes[1],'python3 /opt/cloudhub/records.py','efs_read_other')
        if second is None:return False
        if json.loads(result)['sha256']!=json.loads(second)['sha256']:raise HubError('DATA_CHECK_FAILED','Hai máy không đọc cùng nội dung.',409)
        op['result']={**json.loads(second),'verified_on_nodes':nodes};return True
    def resume(self,env):
        for node in self.nodes(env):
            self.ssm.send_command(InstanceIds=[node],DocumentName='AWS-RunShellScript',Parameters={'commands':['mount -o remount,rw /shared']},TimeoutSeconds=120)
    def audit_backups(self,env,backups,report):
        known={r['recovery_point']:r for r in backups}
        for page in self.awsbackup.get_paginator('list_recovery_points_by_backup_vault').paginate(BackupVaultName=self.settings['backup_vault']):
            for point in page['RecoveryPoints']:
                if point.get('ResourceType')!='EFS' or point['ResourceArn']!=env.get('data_source_arn'):continue
                tags=self.awsbackup.list_tags(ResourceArn=point['RecoveryPointArn'])['Tags'];rp=known.get(tags.get('cloudhub:recovery'))
                if tags.get('cloudhub:owner')!=env['owner_id'] or tags.get('cloudhub:environment')!=env['environment_id']:continue
                retain=rp and rp['status']=='AVAILABLE' and (rp['retain_until']>time.time() or rp.get('restore_lock'))
                report['retained' if retain else 'residual'].append({'type':'Backup::EFSRecoveryPoint','id':point['RecoveryPointArn']})
    def expire_backup(self,rp):
        arn=rp.get('recovery_point_arn')
        if not arn and rp.get('backup_job_id'):
            job=self.awsbackup.describe_backup_job(BackupJobId=rp['backup_job_id'])
            if job['State'] in {'FAILED','ABORTED','EXPIRED'}:return True
            arn=job.get('RecoveryPointArn')
        if not arn:
            # Reconcile response loss by exact recovery tag. Never assume missing metadata means no backup.
            for page in self.awsbackup.get_paginator('list_recovery_points_by_backup_vault').paginate(BackupVaultName=self.settings['backup_vault']):
                for point in page['RecoveryPoints']:
                    tags=self.awsbackup.list_tags(ResourceArn=point['RecoveryPointArn'])['Tags']
                    if tags.get('cloudhub:recovery')==rp['recovery_point'] and tags.get('cloudhub:owner')==rp['owner_id'] and tags.get('cloudhub:environment')==rp['environment_id']:
                        arn=point['RecoveryPointArn'];break
                if arn:break
            if not arn:return False
        try:tags=self.awsbackup.list_tags(ResourceArn=arn)['Tags']
        except Exception as exc:
            if code(exc)=='ResourceNotFoundException':return True
            raise
        if tags.get('cloudhub:owner')!=rp['owner_id'] or tags.get('cloudhub:recovery')!=rp['recovery_point'] or tags.get('cloudhub:environment')!=rp['environment_id']:
            raise HubError('RESOURCE_SCOPE_MISMATCH','EFS bản sao ngoài phạm vi.',409)
        self.awsbackup.delete_recovery_point(BackupVaultName=self.settings['backup_vault'],RecoveryPointArn=arn);return False
