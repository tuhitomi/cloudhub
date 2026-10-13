"""Failed provisioning retries preserve owned stacks, data, quota and fences."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from cloudhub.stack_driver import StackDriver
from cloudhub.service import Service
from cloudhub.store import MemoryStore
from cloudhub.cost import Estimator
from cloudhub.errors import HubError
from cloudhub.worker import Worker
from generate_infra import build_template
from test_processors import rates
from test_domain import Driver

class CF:
    def __init__(self,status='CREATE_FAILED',owner='alice'):
        self.status,self.owner,self.calls=status,owner,[]
    def describe_stacks(self,**kwargs):
        return {'Stacks':[{'StackStatus':self.status,'Outputs':[],
            'Tags':[{'Key':'cloudhub:environment','Value':'env-test'},{'Key':'cloudhub:owner','Value':self.owner}]}]}
    def update_stack(self,**kwargs):self.calls.append(kwargs)

class ConstructionRetryTests(unittest.TestCase):
    def env(self):return {'environment_id':'env-test','owner_id':'alice','template_id':'T08','resources':[]}
    def test_retry_keeps_existing_template_and_successful_resources(self):
        cf=CF();driver=StackDriver({'cf_role_arn':'approved-role'},{'cloudformation':cf})
        driver.create(self.env(),{'operation_id':'op-test','generation':1},None)
        self.assertEqual(cf.calls,[{'StackName':'cloudhub-env-test','UsePreviousTemplate':True,'DisableRollback':True,
            'Capabilities':['CAPABILITY_NAMED_IAM'],'ClientRequestToken':'op-test-g1','RoleARN':'approved-role'}])
    def test_wrong_owner_cannot_retry_stack(self):
        cf=CF(owner='bob');driver=StackDriver({'cf_role_arn':'approved-role'},{'cloudformation':cf})
        with self.assertRaises(HubError):driver.create(self.env(),{'operation_id':'op-test'},None)
        self.assertEqual(cf.calls,[])
    def test_response_loss_does_not_repeat_in_progress_update(self):
        cf=CF('UPDATE_IN_PROGRESS');driver=StackDriver({'cf_role_arn':'approved-role'},{'cloudformation':cf})
        driver.create(self.env(),{'operation_id':'op-test'},None);self.assertEqual(cf.calls,[])
    def test_recovered_stack_still_requires_data_readiness(self):
        cf=CF('UPDATE_COMPLETE');driver=StackDriver({}, {'cloudformation':cf});driver.inventory=lambda _:None
        env=self.env();self.assertTrue(driver.created(env));self.assertEqual(env['outputs'],{})
        self.assertNotIn('ready_at',env)
    def test_product_retry_reuses_environment_quota_and_restarts_create(self):
        store=MemoryStore();service=Service(store,clock=lambda:1000,estimator=Estimator(rates()),verified={'T08'},budget_milliusd=100000)
        op=service.create('alice','create_test',{'template_id':'T08','duration_hours':1})
        row=store.get('O#'+op['operation_id']);row['phase']='WAIT_CREATE';service.save(row)
        Worker(service,Driver()).fail(op['operation_id'],'missing ENI permission')
        before=store.get('Q#alice')
        retried=service.control_operation('alice',op['operation_id'],'retry','retry_test',{})
        self.assertEqual(retried['environment_id'],op['environment_id']);self.assertEqual(retried['phase'],'CREATE')
        self.assertEqual(retried['generation'],1);self.assertEqual(store.get('Q#alice'),before)
        with self.assertRaises(HubError):service.control_operation('bob',op['operation_id'],'retry','cross_test',{})
    def test_create_retry_is_unavailable_for_linux_and_restore(self):
        for template,action in [('T01','create'),('T08','restore')]:
            with self.subTest(template=template,action=action):
                store=MemoryStore();service=Service(store,clock=lambda:1000,estimator=Estimator(rates()),verified={template},budget_milliusd=100000)
                op=service.create('alice','create_test',{'template_id':template,'duration_hours':1})
                row=store.get('O#'+op['operation_id']);row.update(phase='WAIT_CREATE');service.save(row)
                Worker(service,Driver()).fail(op['operation_id'],'failed')
                row=store.get('O#'+op['operation_id']);row['action']=action;service.save(row)
                with self.assertRaises(HubError) as caught:service.control_operation('alice',op['operation_id'],'retry','retry_test',{})
                self.assertEqual(caught.exception.code,'RETRY_UNAVAILABLE')
    def test_efs_network_permissions_stay_in_approved_network_and_deployment_role(self):
        resources=build_template()['Resources'];found={}
        for name,resource in resources.items():
            if name.startswith('EnvironmentPolicy'):
                for statement in resource['Properties']['PolicyDocument']['Statement']:
                    if statement['Action'] in ['ec2:CreateNetworkInterface','ec2:DeleteNetworkInterface']:
                        found[statement['Action']]=statement
                        self.assertEqual(resource['Properties']['Roles'],[{'Ref':'EnvironmentCfRole'}])
        create=found['ec2:CreateNetworkInterface'];delete=found['ec2:DeleteNetworkInterface']
        self.assertNotEqual(create['Resource'],'*')
        self.assertEqual(len([r for r in create['Resource'] if ':subnet/' in r['Fn::Sub']]),2)
        self.assertIn('ec2:Vpc',delete['Condition']['ArnEquals'])
        self.assertEqual(len(delete['Condition']['ArnEquals']['ec2:Subnet']),2)
        self.assertNotIn('CreateNetworkInterface',str(resources['EnvironmentBoundary']))

if __name__=='__main__':unittest.main()
