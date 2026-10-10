"""T10 S3 dataset + native warehouse snapshot; CF restores a new namespace."""
import base64,csv,hashlib,io,json,time
from .ecs_driver import ECSDriver
from .redshift_template import redshift_template
from .cf_common import name
from .s3_driver import canonical
from .s3_files import sha
from .aws_driver import code
from .errors import HubError

class RedshiftDriver(ECSDriver):
    @property
    def warehouse(self):return getattr(self,'redshift-serverless')
    @property
    def data_api(self):return getattr(self,'redshift-data')
    def snapshot(self,rp):
        identifier=rp.get('redshift_snapshot_name') or 'cloudhub-'+rp['recovery_point']
        detail=self.warehouse.get_snapshot(snapshotName=identifier)['snapshot']
        tags={v['key']:v['value'] for v in self.warehouse.list_tags_for_resource(resourceArn=detail['snapshotArn'])['tags']}
        if tags.get('cloudhub:recovery')!=rp['recovery_point'] or tags.get('cloudhub:owner')!=rp['owner_id'] or tags.get('cloudhub:environment')!=rp['environment_id']:
            raise HubError('RESOURCE_SCOPE_MISMATCH','Redshift snapshot ngoài phạm vi.',409)
        return detail
    def build_template(self,env,recovery):
        if recovery:
            snap=self.snapshot(recovery)
            if snap['status']!='AVAILABLE':raise HubError('RECOVERY_UNAVAILABLE','Snapshot chưa sẵn sàng.',409)
        return redshift_template(env,self.settings,recovery)
    def apply_outputs(self,env):
        env.update(bucket_name=env['outputs']['BucketName'],namespace_name=env['outputs']['NamespaceName'],data_source=env['outputs']['NamespaceName'],
            access_address='Kho dữ liệu riêng tư: nạp S3 và truy vấn qua CloudHub')
    def inventory(self,env):
        # Skip ECS-specific discovery while keeping deterministic S3 inventory.
        from .stack_driver import StackDriver
        StackDriver.inventory(self,env);self.file_driver().inventory(env);return env['resources']
    def scoped(self,env):
        detail=self.warehouse.get_namespace(namespaceName=env.get('namespace_name') or name(env,'-ns'))['namespace']
        tags={v['key']:v['value'] for v in self.warehouse.list_tags_for_resource(resourceArn=detail['namespaceArn'])['tags']}
        if tags.get('cloudhub:environment')!=env['environment_id'] or tags.get('cloudhub:owner')!=env['owner_id']:
            raise HubError('RESOURCE_SCOPE_MISMATCH','Namespace không thuộc môi trường.',409)
        env.update(namespace_name=detail['namespaceName'],data_source=detail['namespaceName'])
        if detail.get('adminPasswordSecretArn'):
            env['warehouse_secret']=detail['adminPasswordSecretArn']
            self.tag_secret(env,detail['adminPasswordSecretArn'])
            if not any(v['id']==detail['adminPasswordSecretArn'] for v in env['resources']):env['resources'].append({'type':'AWS::SecretsManager::Secret','id':detail['adminPasswordSecretArn']})
        return detail
    def sql(self,env,op,field,sqls,read=False):
        self.scoped(env)
        params={'WorkgroupName':env['outputs']['WorkgroupName'],'Database':'cloudhub','SecretArn':env['warehouse_secret'],
            'ClientToken':hashlib.sha256((op['operation_id']+field+('-r'+str(op['generation']) if op.get('generation') else '')).encode()).hexdigest()}
        if not op.get(field):
            response=self.data_api.execute_statement(**params,Sql=sqls[0]) if read else self.data_api.batch_execute_statement(**params,Sqls=sqls)
            op[field]=response['Id'];return None
        detail=self.data_api.describe_statement(Id=op[field])
        if detail['Status'] in {'FAILED','ABORTED'}:raise HubError('DATA_CHECK_FAILED',detail.get('Error','Warehouse query failed'),409)
        if detail['Status']!='FINISHED':return None
        if not read:return True
        result=self.data_api.get_statement_result(Id=op[field]);rows=result['Records']
        if result.get('NextToken') or len(rows)>32:raise HubError('DATA_QUOTA','Kho dữ liệu vượt 32 dòng đã duyệt; giữ nguồn.',409)
        return [{'record_id':r[0]['stringValue'],'text':r[1]['stringValue']} for r in rows]
    def ready(self,env,op,recovery):
        if self.scoped(env)['status']!='AVAILABLE':return False
        if self.warehouse.get_workgroup(workgroupName=env['outputs']['WorkgroupName'])['workgroup']['status']!='AVAILABLE':return False
        namespace=self.scoped(env);copy_role=env['outputs']['CopyRole']
        if namespace.get('defaultIamRoleArn')!=copy_role:
            self.warehouse.update_namespace(namespaceName=env['namespace_name'],iamRoles=[copy_role],defaultIamRoleArn=copy_role)
            return False
        # Reconcile the usage limit before CREATE succeeds. Lost responses don't add duplicates.
        arn=env['outputs']['WorkgroupArn'];limits=self.warehouse.list_usage_limits(resourceArn=arn,usageType='serverless-compute')['usageLimits']
        if not limits:
            limits=[self.warehouse.create_usage_limit(resourceArn=arn,usageType='serverless-compute',period='daily',amount=8,breachAction='deactivate')['usageLimit']]
        if len(limits)!=1 or limits[0]['amount']!=8 or limits[0]['breachAction']!='deactivate':raise HubError('COST_GUARD_MISMATCH','Giới hạn Redshift không đúng cấu hình.',409)
        limit=limits[0]
        if not any(v['id']==limit['usageLimitId'] for v in env['resources']):env['resources'].append({'type':'Redshift::UsageLimit','id':limit['usageLimitId']})
        if not self.file_driver().ready(env,op,recovery):return False
        if not recovery:
            if self.sql(env,op,'warehouse_init',['CREATE TABLE IF NOT EXISTS records (record_id VARCHAR(64), text VARCHAR(256));']) is None:return False
        rows=self.sql(env,op,'warehouse_ready',['SELECT record_id,text FROM records ORDER BY record_id LIMIT 33'],read=True)
        if rows is None:return False
        if recovery and sha(canonical(rows))!=recovery['data_sha256']:raise HubError('RESTORE_DATA_MISMATCH','Dữ liệu warehouse khôi phục khác bản sao.',409)
        return True
    def use_data(self,env,op):
        if op['action']=='load_dataset':
            if not op.get('loaded_csv'):
                stream=io.StringIO(newline='');writer=csv.writer(stream);writer.writerow(['record_id','text']);writer.writerow([op['data']['record_id'],op['data']['text']])
                blob=stream.getvalue().encode()
                fileop={'operation_id':op['operation_id'],'file':{'key':'dataset.csv','content_base64':base64.b64encode(blob).decode(),'content_type':'text/plain'}}
                self.file_driver().write_file(env,fileop);op['loaded_csv']=True
            uri='s3://'+env['bucket_name']+'/dataset.csv'
            if self.sql(env,op,'warehouse_copy',['CREATE TABLE IF NOT EXISTS records (record_id VARCHAR(64), text VARCHAR(256));','DELETE FROM records;',
                "COPY records FROM '"+uri+"' IAM_ROLE '"+env['outputs']['CopyRole']+"' CSV IGNOREHEADER 1;"]) is None:return False
        rows=self.sql(env,op,'warehouse_query',['SELECT record_id,text FROM records ORDER BY record_id LIMIT 33'],read=True)
        if rows is None:return False
        op['result']={'items':rows,'query':'SELECT record_id,text FROM records ORDER BY record_id LIMIT 33'};return True
    def backup(self,env,op,rp):
        if not rp.get('data_sha256'):
            rows=self.sql(env,op,'warehouse_manifest',['SELECT record_id,text FROM records ORDER BY record_id LIMIT 33'],read=True)
            if rows is None:return False
            rp.update(data_sha256=sha(canonical(rows)),record_count=len(rows))
        if not self.file_driver().backup(env,op,rp):return False
        identifier='cloudhub-'+rp['recovery_point']
        try:snap=self.snapshot(rp)
        except Exception as exc:
            if code(exc)!='ResourceNotFoundException':raise
            self.warehouse.create_snapshot(namespaceName=env['namespace_name'],snapshotName=identifier,retentionPeriod=7,
                tags=[{'key':'cloudhub:environment','value':env['environment_id']},{'key':'cloudhub:owner','value':env['owner_id']},{'key':'cloudhub:recovery','value':rp['recovery_point']}])
            rp['redshift_snapshot_name']=identifier;return False
        rp.update(redshift_snapshot_name=identifier,redshift_snapshot_arn=snap['snapshotArn'],source=snap['namespaceArn'])
        if snap['status']=='AVAILABLE':return True
        if snap['status'] in {'FAILED','DELETED'}:raise HubError('BACKUP_FAILED','Redshift snapshot thất bại; giữ nguồn.',409)
        return False
    def delete(self,env,op):
        if env.get('outputs',{}).get('WorkgroupArn'):
            try:limits=self.warehouse.list_usage_limits(resourceArn=env['outputs']['WorkgroupArn'],usageType='serverless-compute')['usageLimits']
            except Exception as exc:
                if code(exc)!='ResourceNotFoundException':raise
                limits=[]
            for limit in limits:
                item={'type':'Redshift::UsageLimit','id':limit['usageLimitId']}
                if not any(v['id']==item['id'] for v in env['resources']):env['resources'].append(item)
        for item in env['resources']:
            if item['type']=='Redshift::UsageLimit':
                try:self.warehouse.delete_usage_limit(usageLimitId=item['id'])
                except Exception as exc:
                    if code(exc)!='ResourceNotFoundException':raise
        return super().delete(env,op)
    def _exists(self,item):
        if item['type']=='Redshift::UsageLimit':return bool(self.warehouse.get_usage_limit(usageLimitId=item['id']))
        return super()._exists(item)
    def audit_backups(self,env,backups,report):
        super().audit_backups(env,backups,report)
        known={v['recovery_point']:v for v in backups}
        for page in self.warehouse.get_paginator('list_snapshots').paginate(namespaceName=env.get('namespace_name') or name(env,'-ns')):
            for snap in page['snapshots']:
                tags={v['key']:v['value'] for v in self.warehouse.list_tags_for_resource(resourceArn=snap['snapshotArn'])['tags']}
                if tags.get('cloudhub:environment')!=env['environment_id'] or tags.get('cloudhub:owner')!=env['owner_id']:continue
                rp=known.get(tags.get('cloudhub:recovery'))
                retain=rp and rp['status']=='AVAILABLE' and (rp['retain_until']>time.time() or rp.get('restore_lock'))
                report['retained' if retain else 'residual'].append({'type':'Redshift::Snapshot','id':snap['snapshotArn'],'retain_until':rp['retain_until'] if rp else None})
    def expire_backup(self,rp):
        done=self.file_driver().expire_backup(rp)
        try:snap=self.snapshot(rp)
        except Exception as exc:
            if code(exc)=='ResourceNotFoundException':return done
            raise
        if snap['status']=='AVAILABLE':self.warehouse.delete_snapshot(snapshotName=snap['snapshotName'])
        return False
