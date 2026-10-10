"""T03/T04 share RDS snapshot and PostgreSQL data verification."""
import base64
import json
import time
import urllib.request,urllib.error
from .stack_driver import StackDriver
from .aws_driver import code
from .errors import HubError
from .rds_template import rds_template

class RDSDriver(StackDriver):
    def build_template(self,env,recovery):return rds_template(env,self.settings,recovery)
    def apply_outputs(self,env):
        output=env['outputs'];env.update(db_id=output['DatabaseId'],access_address=output['WebsiteUrl'],data_source=output['DatabaseId'])
        if output.get('InstanceId'):env['instance_id']=output['InstanceId']
        secret=output.get('SecretArn')
        if secret and not any(r['id']==secret for r in env['resources']):env['resources'].append({'type':'AWS::SecretsManager::Secret','id':secret})
    def discover(self,env):
        db=next((r['id'] for r in env['resources'] if r['type']=='AWS::RDS::DBInstance'),None)
        if db:
            env['db_id'],env['data_source']=db,db
            info=self.rds.describe_db_instances(DBInstanceIdentifier=db)['DBInstances'][0]
            secret=info.get('MasterUserSecret',{}).get('SecretArn')
            if secret:
                self.db(env)
                if info.get('MasterUserSecret',{}).get('SecretStatus')=='active':self.tag_secret(env,secret)
                if not any(v['id']==secret for v in env['resources']):env['resources'].append({'type':'AWS::SecretsManager::Secret','id':secret})
    def db(self,env):
        info=self.rds.describe_db_instances(DBInstanceIdentifier=env['db_id'])['DBInstances'][0]
        tags={v['Key']:v['Value'] for v in self.rds.list_tags_for_resource(ResourceName=info['DBInstanceArn'])['TagList']}
        if tags.get('cloudhub:environment')!=env['environment_id'] or tags.get('cloudhub:owner')!=env['owner_id']:
            raise HubError('RESOURCE_SCOPE_MISMATCH','Database không thuộc môi trường.',409)
        return info
    def select_instance(self,env):
        instances=self.active_instances(env)
        if not instances:return False
        env['instance_id']=instances[0]['InstanceId']
        return True
    def local(self,env,op,path,field,payload=None):
        if op.get(field+'_instance'):
            env['instance_id']=op[field+'_instance']
        else:
            if not self.select_instance(env):return None
            op[field+'_instance']=env['instance_id']
        script='set -eu\n'
        if payload is not None:
            encoded=base64.b64encode(json.dumps(payload,ensure_ascii=False).encode()).decode()
            script+="printf '%s' '"+encoded+"' | base64 --decode | curl --fail --silent --show-error --max-time 10 -H 'Content-Type: application/json' --data-binary @- http://127.0.0.1:8080"+path+'\n'
        else:script+='curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8080'+path+'\n'
        return self.command(env,op,script,field)
    def ready(self,env,op,recovery):
        if self.db(env)['DBInstanceStatus']!='available':return False
        try:result=self.local(env,op,'/internal/manifest','db_ready_command')
        except HubError as exc:
            if exc.code!='DATA_CHECK_FAILED':raise
            op.pop('db_ready_command',None);return False
        if result is None:return False
        value=json.loads(result)
        if recovery and value['sha256']!=recovery['manifest_sha256']:raise HubError('RESTORE_DATA_MISMATCH','Dữ liệu PostgreSQL khôi phục khác bản sao.',409)
        if env['template_id']=='T04':
            output=env['outputs'];health=self.elbv2.describe_target_health(TargetGroupArn=output['TargetGroupArn'])['TargetHealthDescriptions']
            if not health or not all(v['TargetHealth']['State']=='healthy' for v in health):return False
        try:
            with urllib.request.urlopen(env['access_address']+'/health',timeout=5) as response:
                if response.status!=200:return False
        except (urllib.error.URLError,TimeoutError):return False
        return True
    def backup(self,env,op,rp):
        info=self.db(env)
        if 'manifest_sha256' not in rp:
            result=self.local(env,op,'/internal/manifest','db_manifest_command')
            if result is None:return False
            manifest=json.loads(result);rp.update(manifest_sha256=manifest['sha256'],record_count=manifest['count'],source=info['DBInstanceArn'])
        snapshot='cloudhub-'+rp['recovery_point']
        try:detail=self.rds.describe_db_snapshots(DBSnapshotIdentifier=snapshot)['DBSnapshots'][0]
        except Exception as exc:
            if code(exc) not in {'DBSnapshotNotFound','DBSnapshotNotFoundFault'}:raise
            self.rds.create_db_snapshot(DBSnapshotIdentifier=snapshot,DBInstanceIdentifier=env['db_id'],Tags=[
                {'Key':'cloudhub:environment','Value':env['environment_id']},{'Key':'cloudhub:owner','Value':env['owner_id']},{'Key':'cloudhub:recovery','Value':rp['recovery_point']}])
            rp['db_snapshot_id']=snapshot;return False
        tags={v['Key']:v['Value'] for v in self.rds.list_tags_for_resource(ResourceName=detail['DBSnapshotArn'])['TagList']}
        if detail['DBInstanceIdentifier']!=env['db_id'] or tags.get('cloudhub:owner')!=env['owner_id'] or tags.get('cloudhub:recovery')!=rp['recovery_point']:
            raise HubError('RESOURCE_SCOPE_MISMATCH','Snapshot không có đúng nguồn/sở hữu.',409)
        rp.update(db_snapshot_id=snapshot,db_snapshot_arn=detail['DBSnapshotArn'])
        if detail['Status']=='available':return True
        if detail['Status'] in {'failed','deleted'}:raise HubError('BACKUP_FAILED','RDS snapshot thất bại; giữ nguồn.',409)
        return False
    def use_data(self,env,op):
        action=op['action'];payload={**op.get('data',{}),'operation_id':op['operation_id']} if action=='put_record' else None
        result=self.local(env,op,'/internal/records','data_command',payload)
        if result is None:return False
        op['result']=json.loads(result);return True
    def audit_backups(self,env,backups,report):
        known={r['recovery_point']:r for r in backups}
        if not env.get('db_id'):return
        for page in self.rds.get_paginator('describe_db_snapshots').paginate(SnapshotType='manual',DBInstanceIdentifier=env['db_id']):
            for snap in page['DBSnapshots']:
                tags={v['Key']:v['Value'] for v in self.rds.list_tags_for_resource(ResourceName=snap['DBSnapshotArn'])['TagList']}
                if tags.get('cloudhub:environment')!=env['environment_id']:continue
                rp=known.get(tags.get('cloudhub:recovery'))
                retain=rp and rp['status']=='AVAILABLE' and (rp['retain_until']>time.time() or rp.get('restore_lock'))
                item={'type':'RDS::DBSnapshot','id':snap['DBSnapshotIdentifier']}
                if retain:item['retain_until']=rp['retain_until']
                report['retained' if retain else 'residual'].append(item)
    def expire_backup(self,rp):
        identifier=rp.get('db_snapshot_id') or 'cloudhub-'+rp['recovery_point']
        try:snap=self.rds.describe_db_snapshots(DBSnapshotIdentifier=identifier)['DBSnapshots'][0]
        except Exception as exc:
            if code(exc) in {'DBSnapshotNotFound','DBSnapshotNotFoundFault'}:return True
            raise
        tags={v['Key']:v['Value'] for v in self.rds.list_tags_for_resource(ResourceName=snap['DBSnapshotArn'])['TagList']}
        if tags.get('cloudhub:recovery')!=rp['recovery_point'] or tags.get('cloudhub:owner')!=rp['owner_id'] or tags.get('cloudhub:environment')!=rp['environment_id']:
            raise HubError('RESOURCE_SCOPE_MISMATCH','Snapshot retention ngoài phạm vi.',409)
        if snap['Status']=='available':self.rds.delete_db_snapshot(DBSnapshotIdentifier=identifier)
        return False
