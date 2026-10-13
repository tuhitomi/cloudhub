"""Registration identity/quota boundary tests; LOCAL ONLY, not email delivery evidence."""
import json,os,sys,unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import boto3
from botocore.stub import Stubber
from cloudhub import api
from cloudhub.accounts import Accounts
from cloudhub.registration import defaults,set_defaults,ensure_profile
from cloudhub.service import Service
from cloudhub.store import MemoryStore
from cloudhub.errors import HubError

IDENTITY={'owner_id':'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa','email':'new@example.com','email_verified':True,'enabled':True,'status':'CONFIRMED'}

class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.service=Service(MemoryStore(),user_limit=2,total_limit=4,budget_milliusd=120000)
    def test_registration_has_only_user_role_no_credentials_and_admin_default_quota(self):
        set_defaults(self.service,'admin',{'max_active':1,'budget_milliusd':9000})
        result=ensure_profile(self.service,IDENTITY)
        self.assertEqual(result['role'],'user');self.assertEqual(result['limits']['max_active'],1)
        self.assertEqual(result['limits']['user_budget_milliusd'],9000)
        profile=self.service.store.get('U#'+IDENTITY['owner_id'])
        self.assertEqual(profile['cognito_sub'],IDENTITY['owner_id'])
        self.assertTrue({'password','token','access_key','temporary_password'}.isdisjoint(profile))
    def test_concurrent_first_requests_initialize_once_without_resetting_counters(self):
        with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(lambda _:ensure_profile(self.service,IDENTITY),range(16)))
        key='Q#'+IDENTITY['owner_id'];quota=self.service.store.get(key)
        self.assertEqual(quota['version'],1)
        quota.update(active=1,reserved_milliusd=5000);self.service.store.commit([quota],{key:quota['version']})
        self.service.set_limits('admin',IDENTITY['owner_id'],{'max_active':3,'budget_milliusd':20000})
        set_defaults(self.service,'admin',{'max_active':0,'budget_milliusd':0})
        ensure_profile(self.service,IDENTITY);quota=self.service.store.get(key)
        self.assertEqual((quota['active'],quota['reserved_milliusd'],quota['max_active'],quota['budget_milliusd']),(1,5000,3,20000))
    def test_unverified_and_disabled_profiles_do_not_gain_quota(self):
        with self.assertRaises(HubError):ensure_profile(self.service,{**IDENTITY,'email_verified':False})
        self.assertEqual(self.service.store.rows,{})
        key='U#'+IDENTITY['owner_id'];self.service.store.commit([{'pk':key,'enabled':False}],{key:None})
        with self.assertRaises(HubError):ensure_profile(self.service,IDENTITY)
        self.assertIsNone(self.service.store.get('Q#'+IDENTITY['owner_id']))
    def test_default_policy_rejects_role_owner_password_and_out_of_range_values(self):
        for body in [{'max_active':1,'budget_milliusd':10,'role':'admin'}, {'max_active':True,'budget_milliusd':10},
                     {'max_active':5,'budget_milliusd':10},{'max_active':1,'budget_milliusd':120001}]:
            with self.assertRaises(HubError):set_defaults(self.service,'admin',body)
        self.assertEqual(defaults(self.service),{'max_active':2,'budget_milliusd':120000})

class ApiVerificationTests(unittest.TestCase):
    def setUp(self):
        self.service=Service(MemoryStore(),budget_milliusd=10000)
        for fixture in [patch.object(api,'SERVICE',self.service),patch.object(api,'DISPATCH',None),patch.dict(os.environ,{'USER_POOL_ID':'test-pool'})]:
            fixture.start();self.addCleanup(fixture.stop)
        self.provider=patch('cloudhub.accounts.Accounts',autospec=True).start();self.addCleanup(patch.stopall)
        self.provider.return_value.identity.return_value={**IDENTITY,'email_verified':False}
    def event(self,path,method='GET',body=None):
        return {'rawPath':path,'body':json.dumps(body or {}),'requestContext':{'http':{'method':method},
            'authorizer':{'jwt':{'claims':{'sub':IDENTITY['owner_id'],'username':'trusted-username','token_use':'access'}}}}}
    def test_unverified_token_cannot_read_manage_or_create_environment(self):
        for path,method in [('/templates','GET'),('/limits','GET'),('/environments','GET'),('/environments','POST'),('/admin/signup-defaults','POST')]:
            response=api.handler(self.event(path,method));self.assertEqual(response['statusCode'],403)
            self.assertEqual(json.loads(response['body'])['error']['code'],'EMAIL_NOT_VERIFIED')
        self.assertEqual(self.service.store.rows,{})
        response=api.handler(self.event('/account'));self.assertEqual(response['statusCode'],200)
        self.assertFalse(json.loads(response['body'])['email_verified'])
    def test_verified_user_cannot_edit_default_quota_or_assign_role(self):
        self.provider.return_value.identity.return_value=IDENTITY
        response=api.handler(self.event('/admin/signup-defaults','POST',{'max_active':4,'budget_milliusd':10000}))
        self.assertEqual(response['statusCode'],403)
        self.assertIsNone(self.service.store.get('S#SIGNUP_DEFAULTS'))
        self.assertEqual(self.service.store.get('U#'+IDENTITY['owner_id'])['role'],'user')
    def test_provider_failure_fails_closed_without_profile_or_quota(self):
        self.provider.return_value.identity.side_effect=RuntimeError('provider unavailable')
        self.assertEqual(api.handler(self.event('/environments'))['statusCode'],500)
        self.assertEqual(self.service.store.rows,{})

class CognitoIdentityTests(unittest.TestCase):
    def test_provider_attributes_override_forged_or_stale_token_claims(self):
        client=boto3.client('cognito-idp',region_name='us-east-1',aws_access_key_id='local',aws_secret_access_key='local')
        attrs=[{'Name':'sub','Value':IDENTITY['owner_id']},{'Name':'email','Value':IDENTITY['email']},{'Name':'email_verified','Value':'false'}]
        with Stubber(client) as stub:
            stub.add_response('admin_get_user',{'Username':'trusted-username','Enabled':True,'UserStatus':'CONFIRMED','UserAttributes':attrs},
                {'UserPoolId':'us-east-1_test','Username':'trusted-username'})
            self.assertFalse(Accounts('us-east-1_test',client).identity(IDENTITY['owner_id'],'trusted-username')['email_verified'])
            stub.add_response('admin_get_user',{'Username':'trusted-username','Enabled':True,'UserStatus':'CONFIRMED','UserAttributes':attrs},
                {'UserPoolId':'us-east-1_test','Username':'trusted-username'})
            with self.assertRaises(HubError) as exc:Accounts('us-east-1_test',client).identity('different-owner','trusted-username')
            self.assertEqual(exc.exception.status,401)

class RegistrationInfraTests(unittest.TestCase):
    def test_self_signup_requires_email_and_cannot_write_privileged_attributes(self):
        resource=json.loads((Path(__file__).resolve().parents[1]/'infra/control-plane.json').read_text(encoding='utf-8'))['Resources']
        pool=resource['UserPool']['Properties'];client=resource['UserClient']['Properties']
        self.assertFalse(pool['AdminCreateUserConfig']['AllowAdminCreateUserOnly'])
        self.assertEqual(pool['AutoVerifiedAttributes'],['email'])
        self.assertNotIn('LambdaConfig',pool) # No auto-confirm or auto-verify bypass.
        self.assertEqual(client['WriteAttributes'],['email'])
        self.assertEqual(pool['UserAttributeUpdateSettings']['AttributesRequireVerificationBeforeUpdate'],['email'])
