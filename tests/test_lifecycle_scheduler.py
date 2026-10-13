"""Actual SDK shapes and owned schedule/instance scope; LOCAL Stubber only."""
import json,hashlib,sys,unittest
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import boto3
from botocore.stub import Stubber
from cloudhub.lifecycle_scheduler import LifecycleScheduler
from cloudhub.aws_driver import AWSDriver
from cloudhub.errors import HubError

class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.client=boto3.client('scheduler',region_name='us-east-1',aws_access_key_id='local',aws_secret_access_key='local')
        self.settings={'schedule_group':'owned-group','expiry_arn':'arn:aws:lambda:us-east-1:123456789012:function:expiry','scheduler_role':'arn:aws:iam::123456789012:role/scheduler','dlq_arn':'arn:aws:sqs:us-east-1:123456789012:dlq'}
        self.env={'environment_id':'env-'+'a'*24,'deadline':1900000000,'lifecycle_revision':2};self.name=self.env['environment_id']+'-lifecycle';self.helper=LifecycleScheduler(self.client,self.settings)
        self.existing={'Name':self.name,'GroupName':'owned-group','ScheduleExpression':'at(2030-01-01T00:00:00)','ScheduleExpressionTimezone':'UTC','FlexibleTimeWindow':{'Mode':'OFF'},'State':'ENABLED',
            'ActionAfterCompletion':'NONE','Description':'preserve this','StartDate':datetime(2020,1,1,tzinfo=timezone.utc),'EndDate':datetime(2040,1,1,tzinfo=timezone.utc),
            'KmsKeyArn':'arn:aws:kms:us-east-1:123456789012:key/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
            'Target':{'Arn':self.settings['expiry_arn'],'RoleArn':self.settings['scheduler_role'],'Input':json.dumps({'environment_id':self.env['environment_id']}),
                'RetryPolicy':{'MaximumEventAgeInSeconds':123,'MaximumRetryAttempts':2},'DeadLetterConfig':{'Arn':self.settings['dlq_arn']}}}
    def test_update_preserves_every_optional_configuration_and_complete_target(self):
        expected=deepcopy(self.existing);expected['ScheduleExpression']='at('+datetime.fromtimestamp(self.env['deadline'],timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')+')'
        expected['Target']['Input']=json.dumps({'environment_id':self.env['environment_id'],'expected_deadline':self.env['deadline'],'expected_revision':2},sort_keys=True)
        expected['ClientToken']=hashlib.sha256((self.name+'|1900000000|2|0').encode()).hexdigest()
        with Stubber(self.client) as stub:
            stub.add_response('get_schedule',self.existing,{'Name':self.name,'GroupName':'owned-group'})
            stub.add_response('update_schedule',{'ScheduleArn':'arn:aws:scheduler:us-east-1:123456789012:schedule/owned-group/'+self.name},expected)
            self.helper.schedule(self.env)
            self.assertEqual(self.env['schedule_synced_revision'],2)
    def test_wrong_environment_target_cannot_be_overwritten(self):
        other=deepcopy(self.existing);other['Target']['Input']=json.dumps({'environment_id':'env-other'})
        with Stubber(self.client) as stub:
            stub.add_response('get_schedule',other,{'Name':self.name,'GroupName':'owned-group'})
            with self.assertRaises(HubError) as error:self.helper.schedule(self.env)
            self.assertEqual(error.exception.code,'SCHEDULE_SCOPE_MISMATCH')
    def test_cancel_missing_schedules_is_idempotent_and_never_deletes_unknown_names(self):
        with Stubber(self.client) as stub:
            for name in sorted({self.name,self.env['environment_id']+'-1900000000'}):
                stub.add_client_error('get_schedule','ResourceNotFoundException',expected_params={'Name':name,'GroupName':'owned-group'})
            self.helper.cancel(self.env)
    def test_pause_rejects_unsupported_templates_without_sdk_mutation(self):
        driver=AWSDriver.__new__(AWSDriver);driver.ec2=Mock();driver.cloudformation=Mock()
        with self.assertRaises(HubError):driver.pause({'template_id':'T04'})
        driver.ec2.stop_instances.assert_not_called();driver.cloudformation.delete_stack.assert_not_called()

if __name__=='__main__':unittest.main()
