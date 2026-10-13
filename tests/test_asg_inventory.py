"""ASG physical names may precede creation; metadata errors must stay visible."""
import sys,unittest
from copy import deepcopy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from botocore.exceptions import ClientError
from cloudhub.stack_driver import StackDriver
from cloudhub.errors import HubError
from generate_infra import build_template

ENV={'environment_id':'env-'+'a'*24,'owner_id':'alice','template_id':'T04','resources':[]}
ASG='cloudhub-'+ENV['environment_id']+'-asg'
TAGS=[{'Key':'cloudhub:environment','Value':ENV['environment_id']},{'Key':'cloudhub:owner','Value':'alice'}]

class Pages:
    def __init__(self,pages,error=None):self.pages,self.error=pages,error
    def paginate(self,**kwargs):
        if self.error:raise self.error
        return iter(self.pages)

class CF:
    def describe_stacks(self,**kwargs):return {'Stacks':[{'StackStatus':'CREATE_IN_PROGRESS','Tags':TAGS}]}
    def get_paginator(self,name):return Pages([{'StackResourceSummaries':[{'PhysicalResourceId':ASG,
        'LogicalResourceId':'ScalingGroup','ResourceType':'AWS::AutoScaling::AutoScalingGroup','ResourceStatus':'CREATE_IN_PROGRESS'}]}])

class EC2:
    def get_paginator(self,name):return Pages([{'Reservations':[]}])

class AS:
    def __init__(self,groups=None,error=None):self.groups,self.error,self.calls=groups or [],error,[]
    def describe_auto_scaling_groups(self,**kwargs):
        self.calls.append(('group',kwargs))
        return {'AutoScalingGroups':deepcopy(self.groups)}
    def get_paginator(self,name):
        self.calls.append(('policies',name))
        return Pages([{'ScalingPolicies':[{'Alarms':[{'AlarmName':'owned-target-tracking'}]}]}],self.error)

class ASGInventoryTests(unittest.TestCase):
    def driver(self,asg):return StackDriver({}, {'cloudformation':CF(),'ec2':EC2(),'autoscaling':asg})
    def test_in_progress_group_name_does_not_fail_construction_or_drop_inventory(self):
        asg=AS();env=deepcopy(ENV);driver=self.driver(asg)
        self.assertFalse(driver.created(env))
        self.assertIn(ASG,[r['id'] for r in env['resources']])
        self.assertEqual(asg.calls,[('group',{'AutoScalingGroupNames':[ASG]})])
        self.assertNotIn('ready_at',env)
    def test_owned_group_discovers_scaling_alarms_without_duplicates(self):
        asg=AS([{'AutoScalingGroupName':ASG,'Tags':TAGS}]);env=deepcopy(ENV);driver=self.driver(asg)
        driver.inventory(env);driver.inventory(env)
        self.assertEqual([r['id'] for r in env['resources'] if r['type']=='AWS::CloudWatch::Alarm'],['owned-target-tracking'])
    def test_group_metadata_access_denied_is_not_absence(self):
        asg=AS()
        error=ClientError({'Error':{'Code':'AccessDenied','Message':'denied'}},'DescribeAutoScalingGroups')
        def denied(**kwargs):raise error
        asg.describe_auto_scaling_groups=denied
        with self.assertRaises(ClientError):self.driver(asg).inventory(deepcopy(ENV))
        self.assertEqual(asg.calls,[])
    def test_policy_access_denied_and_unconfirmed_not_found_remain_errors(self):
        for code,message in [('AccessDenied','denied'),('ValidationError',f'Group {ASG} not found')]:
            with self.subTest(code=code):
                asg=AS([{'AutoScalingGroupName':ASG,'Tags':TAGS}],ClientError({'Error':{'Code':code,'Message':message}},'DescribePolicies'))
                with self.assertRaises(ClientError):self.driver(asg).inventory(deepcopy(ENV))
    def test_delete_race_requires_fresh_confirmed_absence_and_foreign_owner_is_rejected(self):
        error=ClientError({'Error':{'Code':'ValidationError','Message':f'Group {ASG} not found'}},'DescribePolicies')
        asg=AS([{'AutoScalingGroupName':ASG,'Tags':TAGS}],error);describe=asg.describe_auto_scaling_groups
        def removed(**kwargs):
            result=describe(**kwargs);asg.groups=[];return result
        asg.describe_auto_scaling_groups=removed
        self.driver(asg).inventory(deepcopy(ENV))
        foreign=AS([{'AutoScalingGroupName':ASG,'Tags':[{'Key':'cloudhub:environment','Value':ENV['environment_id']},{'Key':'cloudhub:owner','Value':'bob'}]}])
        with self.assertRaises(HubError) as caught:self.driver(foreign).inventory(deepcopy(ENV))
        self.assertEqual(caught.exception.code,'RESOURCE_SCOPE_MISMATCH')
        self.assertFalse(any(kind=='policies' for kind,value in foreign.calls))
    def test_launch_template_run_permission_is_tagged_regional_and_only_for_deployment_role(self):
        resources=build_template()['Resources'];matches=[]
        for name,resource in resources.items():
            if name.startswith('EnvironmentPolicy'):
                for statement in resource['Properties']['PolicyDocument']['Statement']:
                    if statement['Action']=='ec2:RunInstances' and 'launch-template/' in str(statement['Resource']):
                        matches.append(statement)
                        self.assertEqual(resource['Properties']['Roles'],[{'Ref':'EnvironmentCfRole'}])
        self.assertEqual(len(matches),1)
        self.assertEqual(matches[0]['Resource'],{'Fn::Sub':'arn:${AWS::Partition}:ec2:${AWS::Region}:${AWS::AccountId}:launch-template/*'})
        self.assertEqual(matches[0]['Condition'],{'StringEquals':{'aws:RequestedRegion':{'Ref':'AWS::Region'},
            'aws:ResourceTag/cloudhub:managed':'true'},'StringLike':{'aws:ResourceTag/cloudhub:environment':'env-*'}})
        self.assertNotIn('launch-template/',str(resources['EnvironmentBoundary']))

if __name__=='__main__':unittest.main()
