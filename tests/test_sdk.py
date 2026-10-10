"""Botocore Stubber verifies real AWS SDK shapes, still LOCAL MOCK only."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
try:
    import boto3
    from botocore.stub import Stubber
    SDK=True
except ImportError: SDK=False
from cloudhub.aws_driver import AWSDriver
from cloudhub.errors import HubError

@unittest.skipUnless(SDK,'Install requirements-dev.txt for SDK shape checks')
class SDKTests(unittest.TestCase):
    def setUp(self):
        self.clients={name:boto3.client(name,region_name='us-east-1',aws_access_key_id='testing',aws_secret_access_key='testing')
                      for name in ['cloudformation','ec2','ssm','scheduler','backup']}
        self.settings={'region':'us-east-1','account_id':'123456789012','backup_vault':'test-vault','backup_role':'arn:aws:iam::123456789012:role/Backup',
            'schedule_group':'test-group','scheduler_role':'arn:aws:iam::123456789012:role/Scheduler','expiry_arn':'arn:aws:lambda:us-east-1:123456789012:function:Expiry','dlq_arn':'arn:aws:sqs:us-east-1:123456789012:dlq'}
        self.driver=AWSDriver(self.settings,self.clients)
        self.env={'environment_id':'env-test','owner_id':'alice','instance_id':'i-0123456789abcdef0','data_volume_id':'vol-0123456789abcdef0','template_id':'T01'}
    def test_backup_submission_uses_real_idempotency_parameter(self):
        cf,ec2,backup=[Stubber(self.clients[n]) for n in ['cloudformation','ec2','backup']]
        from datetime import datetime,timezone
        stack={'StackName':'cloudhub-env-test','CreationTime':datetime.now(timezone.utc),'StackStatus':'CREATE_COMPLETE','Tags':[
            {'Key':'cloudhub:environment','Value':'env-test'},{'Key':'cloudhub:owner','Value':'alice'}]}
        cf.add_response('describe_stacks',{'Stacks':[stack]},{'StackName':'cloudhub-env-test'})
        instance={'InstanceId':self.env['instance_id'],'State':{'Name':'stopped'},'Tags':stack['Tags']}
        for _ in range(2):ec2.add_response('describe_instances',{'Reservations':[{'Instances':[instance]}]},{'InstanceIds':[self.env['instance_id']]})
        params={'BackupVaultName':'test-vault','ResourceArn':'arn:aws:ec2:us-east-1:123456789012:volume/'+self.env['data_volume_id'],
            'IamRoleArn':self.settings['backup_role'],'IdempotencyToken':'op-test','StartWindowMinutes':60,'CompleteWindowMinutes':120,
            'RecoveryPointTags':{'cloudhub:environment':'env-test','cloudhub:recovery':'rp-test','cloudhub:owner':'alice'}}
        backup.add_response('start_backup_job',{'BackupJobId':'backup-test'},params)
        rp={'recovery_point':'rp-test','manifest_sha256':'123abc'}
        with cf,ec2,backup:
            self.assertFalse(self.driver.backup(self.env,{'operation_id':'op-test'},rp))
            self.assertEqual(rp['backup_job_id'],'backup-test')
        for stub in [cf,ec2,backup]:stub.assert_no_pending_responses()
    def test_wrong_stack_owner_prevents_delete(self):
        from datetime import datetime,timezone
        cf=Stubber(self.clients['cloudformation'])
        cf.add_response('describe_stacks',{'Stacks':[{'StackName':'cloudhub-env-test','CreationTime':datetime.now(timezone.utc),'StackStatus':'CREATE_COMPLETE','Tags':[{'Key':'cloudhub:owner','Value':'bob'}]}]},{'StackName':'cloudhub-env-test'})
        with cf:
            with self.assertRaises(HubError) as ctx:self.driver.delete(self.env,{'operation_id':'op-test'})
            self.assertEqual(ctx.exception.code,'RESOURCE_SCOPE_MISMATCH')
    def test_owned_ssh_key_requires_keypair_tags_and_correct_parameter(self):
        from cloudhub.access import ssh_key
        from cloudhub.service import Service
        from cloudhub.store import MemoryStore
        store=MemoryStore();env={'pk':'E#env-test','kind':'environment','environment_id':'env-test','owner_id':'alice',
            'status':'READY','deadline':2000,'lock':None,'key_pair_id':'key-0123456789abcdef0','public_ip':'192.0.2.10'}
        store.commit([env],{'E#env-test':None});service=Service(store,clock=lambda:1000)
        ec2,ssm=Stubber(self.clients['ec2']),Stubber(self.clients['ssm'])
        ec2.add_response('describe_key_pairs',{'KeyPairs':[{'KeyPairId':env['key_pair_id'],'KeyName':'cloudhub-env-test',
            'Tags':[{'Key':'cloudhub:environment','Value':'env-test'},{'Key':'cloudhub:owner','Value':'alice'}]}]}, {'KeyPairIds':[env['key_pair_id']]})
        ssm.add_response('get_parameter',{'Parameter':{'Name':'/ec2/keypair/'+env['key_pair_id'],'Type':'SecureString','Value':'MOCK_PRIVATE_KEY'}},
            {'Name':'/ec2/keypair/'+env['key_pair_id'],'WithDecryption':True})
        with ec2,ssm:
            with self.assertRaises(HubError):ssh_key(service,'bob','env-test',self.clients)
            result=ssh_key(service,'alice','env-test',self.clients)
            self.assertEqual(result['private_key'],'MOCK_PRIVATE_KEY')
        service.clock=lambda:2000
        with self.assertRaises(HubError):ssh_key(service,'alice','env-test',self.clients)
    def test_ssh_key_denies_foreign_resource_tags_before_decryption(self):
        from cloudhub.access import ssh_key
        from cloudhub.service import Service
        from cloudhub.store import MemoryStore
        store=MemoryStore();store.commit([{'pk':'E#env-test','owner_id':'alice','status':'READY','deadline':2000,'lock':None,
            'key_pair_id':'key-0123456789abcdef0','public_ip':'192.0.2.10'}],{'E#env-test':None})
        ec2=Stubber(self.clients['ec2'])
        ec2.add_response('describe_key_pairs',{'KeyPairs':[{'KeyPairId':'key-0123456789abcdef0','KeyName':'other',
            'Tags':[{'Key':'cloudhub:environment','Value':'env-other'},{'Key':'cloudhub:owner','Value':'alice'}]}]}, {'KeyPairIds':['key-0123456789abcdef0']})
        with ec2:
            with self.assertRaises(HubError) as error:ssh_key(Service(store,clock=lambda:1000),'alice','env-test',self.clients)
            self.assertEqual(error.exception.code,'RESOURCE_SCOPE_MISMATCH')
    def test_scheduler_idempotency_and_disabled_flexible_window(self):
        scheduler=Stubber(self.clients['scheduler']);self.env['deadline']=3600
        params={'Name':'env-test-3600','GroupName':'test-group','ScheduleExpression':'at(1970-01-01T01:00:00)','ScheduleExpressionTimezone':'UTC',
            'FlexibleTimeWindow':{'Mode':'OFF'},'ActionAfterCompletion':'DELETE','ClientToken':'env-test-3600',
            'Target':{'Arn':self.settings['expiry_arn'],'RoleArn':self.settings['scheduler_role'],'Input':'{"environment_id": "env-test"}',
                      'RetryPolicy':{'MaximumEventAgeInSeconds':3600,'MaximumRetryAttempts':3},'DeadLetterConfig':{'Arn':self.settings['dlq_arn']}}}
        scheduler.add_response('create_schedule',{'ScheduleArn':'arn:aws:scheduler:us-east-1:123456789012:schedule/test-group/env-test-3600'},params)
        with scheduler:self.driver.schedule(self.env)
    def test_dynamodb_transaction_contains_all_quota_and_lock_conditions(self):
        from cloudhub.store import DynamoStore
        from cloudhub.service import Service
        from cloudhub.cost import Estimator
        from test_domain import RATES
        client=boto3.client('dynamodb',region_name='us-east-1',aws_access_key_id='testing',aws_secret_access_key='testing')
        calls=[]
        original=client.transact_write_items
        client.get_item=lambda **kw:{}
        client.transact_write_items=lambda **kw:calls.append(kw)
        svc=Service(DynamoStore('table',client),estimator=Estimator(RATES),verified={'T01'},budget_milliusd=10000)
        # create() rereads operation after commit, emulate the returned item after capture.
        def get(**kw):
            if calls:
                for action in calls[-1]['TransactItems']:
                    if action['Put']['Item']['pk']==kw['Key']['pk']:return {'Item':action['Put']['Item']}
            return {}
        client.get_item=get
        svc.create('alice','sdk_test',{'template_id':'T01','duration_hours':1})
        self.assertEqual(len(calls),1)
        actions=calls[0]['TransactItems'];self.assertEqual(len(actions),5)
        self.assertEqual({a['Put']['ConditionExpression'] for a in actions},{'attribute_not_exists(pk)'})
        keys={a['Put']['Item']['pk']['S'] for a in actions}
        self.assertTrue({'Q#GLOBAL','Q#alice'}<=keys)
        from botocore.validate import validate_parameters
        validate_parameters(calls[0],client.meta.service_model.operation_model('TransactWriteItems').input_shape)

if __name__=='__main__':unittest.main()
