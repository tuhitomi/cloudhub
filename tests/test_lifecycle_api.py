"""HTTP adapter integration in local memory, with mocked trusted Cognito identity."""
import os,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from cloudhub import api
import test_lifecycle

class LifecycleApiTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_lifecycle.LifecycleTests();self.fixture.setUp();self.env=self.fixture.ready()
        for setting in [patch.object(api,'SERVICE',self.fixture.service),patch.object(api,'DISPATCH',None),patch.dict(os.environ,{'USER_POOL_ID':'test-pool'})]:
            setting.start();self.addCleanup(setting.stop)
        self.provider=patch('cloudhub.accounts.Accounts',autospec=True);provider=self.provider.start();self.addCleanup(self.provider.stop)
        provider.return_value.identity.side_effect=lambda sub,name:{'owner_id':sub,'email':sub+'@example.com','email_verified':True}
    def request(self,path,body=None,owner='alice',method='POST'):
        return api.handler({'rawPath':path,'headers':{'Idempotency-Key':'valid-lifecycle-key'},'body':json.dumps(body or {}),
            'requestContext':{'http':{'method':method},'authorizer':{'jwt':{'claims':{'sub':owner,'username':owner,'token_use':'access'}}}}})
    def test_forecast_has_separate_period_and_long_renewal_returns_202_operation(self):
        quote=self.request('/estimates',{'template_id':'T05','estimate_hours':8,'operating_policy':self.fixture.policy()})
        self.assertEqual(quote['statusCode'],200);self.assertEqual(json.loads(quote['body'])['operating_policy']['period_minutes'],5)
        renewed=self.request('/environments/'+self.env['environment_id']+'/renew',{'minutes':5,'confirmed':True,'expected_revision':self.env['lifecycle_revision']})
        self.assertEqual(renewed['statusCode'],202);self.assertIn('operation_id',json.loads(renewed['body']))
        self.assertEqual(self.fixture.driver.calls.count('create'),1)
    def test_cross_owner_and_injected_owner_do_not_mutate_environment(self):
        path='/environments/'+self.env['environment_id']+'/renew'
        response=self.request(path,{'minutes':5,'confirmed':True},owner='bob');self.assertEqual(response['statusCode'],404)
        response=self.request(path,{'minutes':5,'confirmed':True,'owner_id':'alice'},owner='bob');self.assertEqual(response['statusCode'],400)
        self.assertEqual(self.fixture.store.get(self.env['pk'])['deadline'],self.env['deadline'])

if __name__=='__main__':unittest.main()
