"""T09 consistent bounded dataset, transactional write retry, checked logical restore."""
from .stack_driver import StackDriver
from .archive import ArchiveMixin
from .data_template import data_template
from .s3_driver import canonical
from .s3_files import sha,error_code
from .errors import HubError
from .cf_common import name
from datetime import datetime,timezone

class DataDriver(ArchiveMixin,StackDriver):
    def build_template(self,env,recovery):return data_template(env,self.settings,recovery)
    def inventory(self,env):
        resources=super().inventory(env)
        stack=self.assert_stack(env)
        # CF can assign the intended table name before a failed CreateTable.
        # Only a never-ready, terminal failed stack and an explicit DynamoDB
        # NotFound can establish that this source was never provisioned.
        expected=name(env,'-data')
        terminal={'CREATE_FAILED','ROLLBACK_COMPLETE','ROLLBACK_FAILED'}
        failed=stack and stack['StackStatus'] in terminal
        prior=env.get('missing_uncreated_source',{})
        previously_missing=(prior.get('id')==expected and prior.get('type')=='AWS::DynamoDB::Table'
            and prior.get('error')=='ResourceNotFoundException' and prior.get('failed_stack_status') in terminal)
        deleting=not stack or stack['StackStatus'] in {'DELETE_IN_PROGRESS','DELETE_COMPLETE'}
        if env.get('ready_at') or not (failed or previously_missing and deleting):return resources
        source=next((r for r in resources if r['type']=='AWS::DynamoDB::Table' and r['id']==expected),None)
        if not source:return resources
        try:self.dynamodb.describe_table(TableName=expected)
        except Exception as exc:
            if error_code(exc)!='ResourceNotFoundException':raise
            env['source_ids']=[rid for rid in env['source_ids'] if rid!=expected]
            env['missing_uncreated_source']={'type':'AWS::DynamoDB::Table','id':expected,'checked_at':int(datetime.now(timezone.utc).timestamp()),'error':'ResourceNotFoundException','failed_stack_status':stack['StackStatus'] if failed else prior['failed_stack_status']}
        return resources
    def audit(self,env,backups):
        self.inventory(env)
        return super().audit(env,backups)
    def apply_outputs(self,env):
        env.update(data_source=env['outputs']['TableName'],access_address=env['outputs']['ApiUrl'])
    def table(self,env):
        table=env.get('data_source') or next((r['id'] for r in env.get('resources',[]) if r['type']=='AWS::DynamoDB::Table'),None)
        if not table:raise HubError('SOURCE_MISSING','Không có bảng dữ liệu.',409)
        detail=self.dynamodb.describe_table(TableName=table)['Table']
        tags={v['Key']:v['Value'] for v in self.dynamodb.list_tags_of_resource(ResourceArn=detail['TableArn'])['Tags']}
        if tags.get('cloudhub:environment')!=env['environment_id'] or tags.get('cloudhub:owner')!=env['owner_id']:
            raise HubError('RESOURCE_SCOPE_MISMATCH','Bảng không thuộc môi trường.',409)
        env['data_source']=table;return table
    def records(self,env):
        params={'TableName':self.table(env),'ConsistentRead':True};rows=[]
        while True:
            response=self.dynamodb.scan(**params)
            rows.extend({'record_id':r['row_id']['S'][2:],'text':r['text']['S']} for r in response['Items'] if r['row_id']['S'].startswith('R#'))
            if len(rows)>32:raise HubError('DATA_QUOTA','Nguồn vượt 32 bản ghi; giữ dữ liệu.',409)
            if not response.get('LastEvaluatedKey'):break
            params['ExclusiveStartKey']=response['LastEvaluatedKey']
        return sorted(rows,key=lambda v:v['record_id'])
    def put(self,env,oid,record):
        table=self.table(env);fingerprint=sha(canonical(record));key={'row_id':{'S':'I#'+oid}}
        prior=self.dynamodb.get_item(TableName=table,Key=key,ConsistentRead=True).get('Item')
        if prior:
            if prior['fingerprint']['S']!=fingerprint:raise HubError('IDEMPOTENCY_MISMATCH','Nội dung khác yêu cầu cũ.',409)
            return
        current=self.records(env)
        if len(current)>=32 and record['record_id'] not in {v['record_id'] for v in current}:raise HubError('DATA_QUOTA','Tối đa 32 bản ghi.',409)
        self.dynamodb.transact_write_items(ClientRequestToken=oid,TransactItems=[
            {'Put':{'TableName':table,'Item':{'row_id':{'S':'R#'+record['record_id']},'text':{'S':record['text']}}}},
            {'Put':{'TableName':table,'Item':{**key,'fingerprint':{'S':fingerprint}},'ConditionExpression':'attribute_not_exists(row_id)'}}])
    def ready(self,env,op,recovery):
        table=self.table(env)
        if self.dynamodb.describe_table(TableName=table)['Table']['TableStatus']!='ACTIVE':return False
        rows=self.load_archive(env,recovery) if recovery else [{'record_id':'demo','text':'CloudHub T09'}]
        if op['action'] in {'create','restore'}:
            cursor=int(op.get('restore_cursor',0))
            for row in rows[cursor:cursor+2]:
                # The new environment has its own table; conditional puts tolerate response loss.
                existing=self.dynamodb.get_item(TableName=table,Key={'row_id':{'S':'R#'+row['record_id']}},ConsistentRead=True).get('Item')
                if existing and existing['text']['S']!=row['text']:raise HubError('RESTORE_DATA_MISMATCH','Bản ghi đã tồn tại với nội dung khác.',409)
                if not existing:self.dynamodb.put_item(TableName=table,Item={'row_id':{'S':'R#'+row['record_id']},'text':{'S':row['text']}},ConditionExpression='attribute_not_exists(row_id)')
                cursor+=1
            op['restore_cursor']=cursor
            if cursor<len(rows):return False
        if recovery and sha(canonical(self.records(env)))!=recovery['data_sha256']:raise HubError('RESTORE_DATA_MISMATCH','Dữ liệu DynamoDB khôi phục khác nguồn.',409)
        function=getattr(self,'lambda').get_function_configuration(FunctionName=env['outputs']['FunctionName'])
        if function['State']!='Active':return False
        routes=self.apigatewayv2.get_routes(ApiId=env['outputs']['ApiId'])['Items']
        return any(r.get('RouteKey')=='GET /records' and r.get('AuthorizationType')=='JWT' for r in routes)
    def use_data(self,env,op):
        if op['action']=='put_record':self.put(env,op['operation_id'],op['data'])
        op['result']={'items':self.records(env)};return True
    def backup(self,env,op,rp):self.save_archive(env,rp,self.records(env));return True
    def audit_backups(self,env,backups,report):self.audit_archive(env,backups,report)
    def expire_backup(self,rp):return self.expire_archive(rp)
