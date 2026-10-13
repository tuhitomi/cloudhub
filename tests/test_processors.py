"""LOCAL ONLY: additional processor ownership, retry, backup/restore and SDK checks."""
import io,json,sys,unittest
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import boto3
from botocore.stub import Stubber
from cloudhub.catalog import IMPLEMENTED,configuration
from cloudhub.cost import Estimator,RATE_REQUIREMENTS
from cloudhub.errors import HubError
from cloudhub.service import Service
from cloudhub.store import MemoryStore
from cloudhub.worker import Worker
from cloudhub.data_driver import DataDriver
from cloudhub.efs_driver import EFSDriver
from cloudhub.rds_driver import RDSDriver
from cloudhub.redshift_driver import RedshiftDriver
from cloudhub.s3_files import OwnedFiles,bucket_name,sha
from cloudhub.s3_driver import canonical
from cloudhub.s3_template import s3_template
from cloudhub.rds_template import rds_template
from cloudhub.ecs_template import ecs_template
from cloudhub.data_template import data_template
from cloudhub.redshift_template import redshift_template
from test_s3 import FakeS3,SETTINGS

S={**SETTINGS,'region':'ap-southeast-1','vpc_id':'vpc-0123456789abcdef0','public_subnet_id':'subnet-0123456789abcdef0',
   'public_subnet_ids':['subnet-0123456789abcdef0','subnet-abcdef01234567890'],'db_subnet_ids':['subnet-11111111111111111','subnet-22222222222222222'],
   'ami_id':'ami-0123456789abcdef0','instance_profile':'shared-ssm','permissions_boundary':'arn:aws:iam::123456789012:policy/boundary',
   'postgres_version':'16.3','cognito_client_id':'clientid','cognito_issuer':'https://cognito-idp.ap-southeast-1.amazonaws.com/ap-southeast-1_abcd',
   'management_table':'cloudhub-meta','management_table_arn':'arn:aws:dynamodb:ap-southeast-1:123456789012:table/cloudhub-meta','frontend_origin':'https://demo.cloudfront.net',
   'approved_image':'123456789012.dkr.ecr.ap-southeast-1.amazonaws.com/cloudhub-demo@sha256:'+'a'*64,'backup_role':'arn:aws:iam::123456789012:role/backup',
   'backup_restore_role':'arn:aws:iam::123456789012:role/restore','backup_vault':'shared-data'}
ENV={'environment_id':'env-'+'a'*24,'owner_id':'alice','template_id':'T09','resources':[]}
OP={'operation_id':'op-'+'b'*24,'action':'create'}
def client(name):return boto3.client(name,region_name='ap-southeast-1',aws_access_key_id='testing',aws_secret_access_key='testing')
def rates():return {'approved':True,'region':'test-only','updated_at':'2026-10-09T00:00:00Z','units':{k:'0.02' for r in RATE_REQUIREMENTS.values() for k in r}}

class FakeData:
    def __init__(self):self.tables={};self.transacts=0;self.owner='alice';self.eid=ENV['environment_id']
    def describe_table(self,TableName):return {'Table':{'TableName':TableName,'TableStatus':'ACTIVE','TableArn':'arn:aws:dynamodb:ap-southeast-1:123456789012:table/'+TableName}}
    def list_tags_of_resource(self,ResourceArn):return {'Tags':[{'Key':'cloudhub:owner','Value':self.owner},{'Key':'cloudhub:environment','Value':self.eid}]}
    def scan(self,TableName,**kw):return {'Items':list(deepcopy(self.tables.setdefault(TableName,{})).values())}
    def get_item(self,TableName,Key,**kw):return {'Item':deepcopy(self.tables.setdefault(TableName,{}).get(Key['row_id']['S']))} if Key['row_id']['S'] in self.tables.setdefault(TableName,{}) else {}
    def put_item(self,TableName,Item,**kw):self.tables.setdefault(TableName,{})[Item['row_id']['S']]=deepcopy(Item)
    def transact_write_items(self,TransactItems,**kw):
        self.transacts+=1
        for row in TransactItems:self.put_item(**row['Put'])

class ProcessorTests(unittest.TestCase):
    def test_all_sources_gated_and_costs_require_their_service_units(self):
        self.assertEqual(len(IMPLEMENTED),10)
        for tid in IMPLEMENTED:
            quote=Estimator(rates()).estimate(tid,1);self.assertTrue(quote['approved']);self.assertGreater(quote['total_milliusd'],0)
            missing=deepcopy(rates());missing['units'].pop(next(iter(RATE_REQUIREMENTS[tid])))
            self.assertFalse(Estimator(missing).estimate(tid,1)['approved'])
            service=Service(MemoryStore(),estimator=Estimator(rates()),budget_milliusd=100000)
            with self.assertRaises(HubError) as error:service.create('alice','gate_'+tid,{'template_id':tid,'duration_hours':1})
            self.assertEqual(error.exception.code,'TEMPLATE_UNVERIFIED')
    def test_record_commands_are_owned_locked_and_idempotent(self):
        service=Service(MemoryStore(),clock=lambda:1000,estimator=Estimator(rates()),verified={'T03'},budget_milliusd=100000)
        created=service.create('alice','create_03',{'template_id':'T03','duration_hours':1});env=service.owned('E',created['environment_id'],'alice')
        env.update(status='READY',lock=None,deadline=2000);service.save(env)
        body={'record_id':'a','text':'dữ liệu'}
        with self.assertRaises(HubError):service.data_command('bob',env['environment_id'],'put_record','cross_03',body)
        op=service.data_command('alice',env['environment_id'],'put_record','write_03',body)
        self.assertEqual(op['operation_id'],service.data_command('alice',env['environment_id'],'put_record','write_03',body)['operation_id'])
        with self.assertRaises(HubError):service.command('alice',env['environment_id'],'backup','backup03')
        with self.assertRaises(HubError):service.data_command('alice',env['environment_id'],'put_record','write_03',{**body,'text':'changed'})
    def test_dynamodb_write_response_retry_does_not_duplicate_transaction(self):
        fake=FakeData();driver=DataDriver(S,{'dynamodb':fake});env={**deepcopy(ENV),'data_source':'cloudhub-'+ENV['environment_id']+'-data'}
        item={'record_id':'demo','text':'test'};driver.put(env,OP['operation_id'],item);driver.put(env,OP['operation_id'],item)
        self.assertEqual(fake.transacts,1);self.assertEqual(driver.records(env),[item])
        fake.owner='bob'
        with self.assertRaises(HubError):driver.put(env,'op-'+'c'*24,item)
        self.assertEqual(fake.transacts,1)
    def test_logical_restore_recreates_records_and_detects_corruption(self):
        fake=FakeData();files=FakeS3();driver=DataDriver(S,{'dynamodb':fake,'s3':files})
        source={**deepcopy(ENV),'data_source':'source-table'};driver.put(source,OP['operation_id'],{'record_id':'demo','text':'unique fixture'})
        rp={**{k:source[k] for k in ['environment_id','owner_id','template_id']},'recovery_point':'rp-'+'c'*24}
        driver.save_archive(source,rp,driver.records(source));new={**deepcopy(ENV),'data_source':'new-table','outputs':{'FunctionName':'f','ApiId':'api'}}
        fake.eid=new['environment_id'];op={'operation_id':'op-'+'d'*24,'action':'restore'}
        function=type('Lambda',(),{'get_function_configuration':lambda self,**kw:{'State':'Active'}})()
        gateway=type('Api',(),{'get_routes':lambda self,**kw:{'Items':[{'RouteKey':'GET /records','AuthorizationType':'JWT'}]}})()
        driver.clients.update({'lambda':function,'apigatewayv2':gateway})
        self.assertTrue(driver.ready(new,op,rp));self.assertEqual(driver.records(new),driver.records(source))
        files.buckets[S['s3_backup_bucket']]['files'][driver.archive_key(rp)][-1]['data']=b'corrupt'
        with self.assertRaises(HubError) as error:driver.ready(new,op,rp)
        self.assertEqual(error.exception.code,'BACKUP_CORRUPT')
    def test_database_snapshot_scope_checked_before_snapshot_success(self):
        rds=client('rds');stub=Stubber(rds);driver=RDSDriver(S,{'rds':rds});env={**ENV,'template_id':'T03','db_id':'cloudhub-env-db'}
        arn='arn:aws:rds:ap-southeast-1:123456789012:db:cloudhub-env-db'
        stub.add_response('describe_db_instances',{'DBInstances':[{'DBInstanceIdentifier':env['db_id'],'DBInstanceArn':arn}]},{'DBInstanceIdentifier':env['db_id']})
        stub.add_response('list_tags_for_resource',{'TagList':[{'Key':'cloudhub:owner','Value':'bob'}]},{'ResourceName':arn})
        with stub:
            with self.assertRaises(HubError) as error:driver.backup(env,deepcopy(OP),{'recovery_point':'rp-'+'c'*24})
            self.assertEqual(error.exception.code,'RESOURCE_SCOPE_MISMATCH')
        stub.assert_no_pending_responses()
    def test_rds_snapshot_sdk_submission_has_one_deterministic_name(self):
        rds=client('rds');stub=Stubber(rds);driver=RDSDriver(S,{'rds':rds});env={**ENV,'template_id':'T03','db_id':'cloudhub-env-db'}
        driver.db=lambda e:{'DBInstanceArn':'arn:aws:rds:ap-southeast-1:123456789012:db:cloudhub-env-db'}
        rp={'recovery_point':'rp-'+'c'*24,'manifest_sha256':'a'*64};identifier='cloudhub-'+rp['recovery_point']
        stub.add_client_error('describe_db_snapshots','DBSnapshotNotFound',expected_params={'DBSnapshotIdentifier':identifier})
        stub.add_response('create_db_snapshot',{'DBSnapshot':{'DBSnapshotIdentifier':identifier}},{'DBSnapshotIdentifier':identifier,'DBInstanceIdentifier':env['db_id'],
            'Tags':[{'Key':'cloudhub:environment','Value':env['environment_id']},{'Key':'cloudhub:owner','Value':'alice'},{'Key':'cloudhub:recovery','Value':rp['recovery_point']}]})
        with stub:self.assertFalse(driver.backup(env,deepcopy(OP),rp))
        stub.assert_no_pending_responses()
    def test_efs_backup_freezes_both_clients_and_uses_idempotent_job(self):
        backup=client('backup');stub=Stubber(backup);driver=EFSDriver(S,{'backup':backup});env={**ENV,'template_id':'T08','node_ids':['i-aaaaaaaaaaaaaaaaa','i-bbbbbbbbbbbbbbbbb']}
        arn='arn:aws:elasticfilesystem:ap-southeast-1:123456789012:file-system/fs-12345678'
        driver.fs=lambda e:{'FileSystemArn':arn};seen=[]
        driver.node_command=lambda e,o,n,s,f:seen.append((n,s,f)) or ''
        rp={'recovery_point':'rp-'+'c'*24,'manifest_sha256':'a'*64}
        stub.add_response('start_backup_job',{'BackupJobId':'11111111-1111-1111-1111-111111111111'},
            {'BackupVaultName':S['backup_vault'],'ResourceArn':arn,'IamRoleArn':S['backup_role'],'IdempotencyToken':OP['operation_id'],'Lifecycle':{'DeleteAfterDays':7},
             'RecoveryPointTags':{'cloudhub:owner':'alice','cloudhub:environment':env['environment_id'],'cloudhub:recovery':rp['recovery_point']}})
        with stub:self.assertFalse(driver.backup(env,deepcopy(OP),rp))
        self.assertEqual({n for n,script,f in seen if 'remount,ro' in script},set(env['node_ids']));stub.assert_no_pending_responses()
    def test_efs_restore_targets_only_new_fs_and_fixes_owner_tags(self):
        backup=client('backup');stub=Stubber(backup);driver=EFSDriver(S,{'backup':backup})
        env={**ENV,'template_id':'T08','file_system_id':'fs-new12345','node_ids':['i-aaaaaaaaaaaaaaaaa','i-bbbbbbbbbbbbbbbbb']}
        driver.fs=lambda e:{'LifeCycleState':'available'}
        driver.node_command=lambda *args:''
        driver.clients['ssm']=type('SSM',(),{'describe_instance_information':lambda self,**kw:{'InstanceInformationList':[{'PingStatus':'Online'}]}})()
        arn='arn:aws:backup:ap-southeast-1:123456789012:recovery-point:11111111-1111-1111-1111-111111111111'
        stub.add_response('start_restore_job',{'RestoreJobId':'22222222-2222-2222-2222-222222222222'},
            {'RecoveryPointArn':arn,'IamRoleArn':S['backup_restore_role'],'IdempotencyToken':OP['operation_id'],'ResourceType':'EFS','CopySourceTagsToRestoredResource':False,
             'Metadata':{'file-system-id':env['file_system_id'],'newFileSystem':'false','Encrypted':'true','PerformanceMode':'generalPurpose','CreationToken':OP['operation_id']}})
        with stub:self.assertFalse(driver.ready(env,deepcopy(OP),{'recovery_point_arn':arn}))
        stub.assert_no_pending_responses()
    def test_efs_restore_waits_for_both_mounts_and_retries_failed_bootstrap(self):
        driver=EFSDriver(S,{});env={**ENV,'template_id':'T08','file_system_id':'fs-0123456789abcdef0',
            'node_ids':['i-aaaaaaaaaaaaaaaaa','i-bbbbbbbbbbbbbbbbb']}
        driver.fs=lambda e:{'LifeCycleState':'available'}
        driver.clients['ssm']=type('SSM',(),{'describe_instance_information':lambda self,**kw:{'InstanceInformationList':[{'PingStatus':'Online'}]}})()
        calls=[]
        def command(e,o,n,s,f):
            calls.append((n,s,f));o[f+'_'+n]='failed-command'
            if n==e['node_ids'][1]:raise HubError('DATA_CHECK_FAILED','Mount not ready',409)
            return ''
        driver.node_command=command
        driver.clients['backup']=type('Backup',(),{'start_restore_job':lambda self,**kw:(_ for _ in ()).throw(AssertionError('Restore started before both mounts'))})()
        op={**OP,'action':'restore'}
        self.assertFalse(driver.ready(env,op,{'recovery_point_arn':'unused'}))
        self.assertNotIn('efs_bootstrap_done',op)
        self.assertNotIn('efs_bootstrap_'+env['node_ids'][1],op)
        self.assertEqual([n for n,s,f in calls],env['node_ids'])
        self.assertTrue(all('mount -t efs -o tls fs-0123456789abcdef0:/' in s for n,s,f in calls))
    def test_share_is_owner_only_version_pinned_and_deadline_bounded(self):
        s3=client('s3');stub=Stubber(s3);service=Service(MemoryStore(),clock=lambda:1000)
        env={**ENV,'template_id':'T07','status':'READY','deadline':1060,'lock':None}
        from cloudhub.service import record
        service.save(record('environment',env['environment_id'],'alice',**{k:v for k,v in env.items() if k!='owner_id'}))
        files=OwnedFiles(service,S,s3);bucket=bucket_name(env,S)
        tags=[{'Key':k,'Value':v} for k,v in {'cloudhub:environment':env['environment_id'],'cloudhub:owner':'alice','cloudhub:managed':'true'}.items()]
        stub.add_response('get_bucket_tagging',{'TagSet':tags},{'Bucket':bucket,'ExpectedBucketOwner':S['account_id']})
        stub.add_response('head_object',{'VersionId':'version-1'},{'Bucket':bucket,'Key':'demo.txt','ExpectedBucketOwner':S['account_id']})
        with stub,patch.object(s3,'generate_presigned_url',return_value='https://signed.invalid') as signer:
            with self.assertRaises(HubError):files.share('bob',env['environment_id'],{'key':'demo.txt'})
            result=files.share('alice',env['environment_id'],{'key':'demo.txt'})
            self.assertEqual(result['expires_at'],1060);self.assertEqual(signer.call_args.kwargs['ExpiresIn'],60)
            self.assertEqual(signer.call_args.kwargs['Params']['VersionId'],'version-1')
        stub.assert_no_pending_responses()
    def test_template_reuse_preserves_scope_and_private_data(self):
        base=rds_template({**ENV,'template_id':'T03'},S);scaled=rds_template({**ENV,'template_id':'T04'},S)
        self.assertEqual(base['Resources']['Database'],scaled['Resources']['Database'])
        self.assertFalse(base['Resources']['Database']['Properties']['PubliclyAccessible'])
        self.assertEqual(scaled['Resources']['ScalingGroup']['Properties']['MaxSize'],'2')
        shared=s3_template({**ENV,'template_id':'T07'},S);self.assertNotIn('Distribution',shared['Resources'])
        with self.assertRaises(HubError):ecs_template({**ENV,'template_id':'T06'},{**S,'approved_image':'nginx:latest'})
        data=data_template(ENV,S);routes=[v for v in data['Resources'].values() if v['Type']=='AWS::ApiGatewayV2::Route']
        self.assertEqual(routes[0]['Properties']['AuthorizationType'],'JWT')
        warehouse=redshift_template({**ENV,'template_id':'T10'},S,{'redshift_snapshot_name':'cloudhub-rp-'+24*'c'})
        self.assertEqual(warehouse['Resources']['Workgroup']['Properties']['SnapshotName'],'cloudhub-rp-'+24*'c')
    def test_redshift_snapshot_owner_is_checked_before_use(self):
        redshift=client('redshift-serverless');stub=Stubber(redshift);driver=RedshiftDriver(S,{'redshift-serverless':redshift})
        rp={'recovery_point':'rp-'+'c'*24,'owner_id':'alice','environment_id':ENV['environment_id']};arn='arn:aws:redshift-serverless:ap-southeast-1:123456789012:snapshot/12345678'
        stub.add_response('get_snapshot',{'snapshot':{'snapshotArn':arn,'snapshotName':'cloudhub-'+rp['recovery_point'],'status':'AVAILABLE'}},{'snapshotName':'cloudhub-'+rp['recovery_point']})
        stub.add_response('list_tags_for_resource',{'tags':[{'key':'cloudhub:owner','value':'bob'}]},{'resourceArn':arn})
        with stub:
            with self.assertRaises(HubError) as error:driver.snapshot(rp)
            self.assertEqual(error.exception.code,'RESOURCE_SCOPE_MISMATCH')
        stub.assert_no_pending_responses()
if __name__=='__main__':unittest.main()
