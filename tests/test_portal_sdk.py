"""Real SDK shape validation with Stubber, not AWS execution evidence."""
import json,os,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import boto3
from botocore.stub import Stubber
from cloudhub.accounts import Accounts,subscription_status
from cloudhub.actual_cost import ActualCosts
from cloudhub.errors import HubError
from cloudhub.store import MemoryStore
from cloudhub.service import Service
from cloudhub.backup_export import download

def client(name):return boto3.client(name,region_name='us-east-1',aws_access_key_id='local',aws_secret_access_key='local')

class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.client=client('cognito-idp');self.stub=Stubber(self.client);self.stub.activate()
        self.pool='us-east-1_demo';self.owner='aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa';self.accounts=Accounts(self.pool,self.client)
        self.attrs=[{'Name':'sub','Value':self.owner},{'Name':'email','Value':'student@example.com'}]
    def tearDown(self):self.stub.assert_no_pending_responses();self.stub.deactivate()
    def test_stale_admin_jwt_cannot_use_admin_api_after_group_removal(self):
        self.stub.add_response('admin_get_user',{'Username':self.owner,'Enabled':True,'UserAttributes':self.attrs},{'UserPoolId':self.pool,'Username':self.owner})
        self.stub.add_response('admin_list_groups_for_user',{'Groups':[]},{'UserPoolId':self.pool,'Username':self.owner})
        with self.assertRaises(HubError) as error:self.accounts.assert_admin(self.owner,self.owner)
        self.assertEqual(error.exception.status,403)
    def test_invitation_uses_pool_scope_and_never_returns_password(self):
        self.stub.add_response('admin_create_user',{'User':{'Username':self.owner,'Enabled':True,'UserStatus':'FORCE_CHANGE_PASSWORD','Attributes':self.attrs}},
            {'UserPoolId':self.pool,'Username':'student@example.com','UserAttributes':[{'Name':'email','Value':'student@example.com'}],'DesiredDeliveryMediums':['EMAIL']})
        result=self.accounts.create({'email':'Student@example.com','send_invitation':True})
        self.assertTrue({'password','temporary_password','TemporaryPassword','access_token'}.isdisjoint(result))
        self.assertEqual(result['status'],'FORCE_CHANGE_PASSWORD')
    def test_disabling_user_fails_closed_when_provider_call_is_denied(self):
        self.stub.add_response('list_users',{'Users':[{'Username':self.owner,'Enabled':True,'Attributes':self.attrs}]},{'UserPoolId':self.pool,'Filter':'sub = "'+self.owner+'"','Limit':2})
        self.stub.add_response('admin_list_groups_for_user',{'Groups':[]},{'UserPoolId':self.pool,'Username':self.owner})
        self.stub.add_client_error('admin_disable_user','AccessDeniedException',expected_params={'UserPoolId':self.pool,'Username':self.owner})
        service=Service(MemoryStore())
        with self.assertRaises(Exception):self.accounts.set_enabled('another-admin',self.owner,False,service)
        self.assertFalse(service.store.get('U#'+self.owner)['enabled'])
    def test_admin_account_cannot_be_disabled(self):
        self.stub.add_response('list_users',{'Users':[{'Username':self.owner,'Enabled':True,'Attributes':self.attrs}]},{'UserPoolId':self.pool,'Filter':'sub = "'+self.owner+'"','Limit':2})
        self.stub.add_response('admin_list_groups_for_user',{'Groups':[{'GroupName':'admin'}]},{'UserPoolId':self.pool,'Username':self.owner})
        with self.assertRaises(HubError):self.accounts.set_enabled('another-admin',self.owner,False,Service(MemoryStore()))
    def test_invitation_retry_after_lost_response_never_resends_email(self):
        body={'email':'student@example.com','send_invitation':True};service=Service(MemoryStore())
        self.stub.add_client_error('admin_create_user','UsernameExistsException',expected_params={'UserPoolId':self.pool,'Username':body['email'],'UserAttributes':[{'Name':'email','Value':body['email']}],'DesiredDeliveryMediums':['EMAIL']})
        self.stub.add_response('admin_get_user',{'Username':self.owner,'Enabled':True,'UserStatus':'FORCE_CHANGE_PASSWORD','UserAttributes':self.attrs},{'UserPoolId':self.pool,'Username':body['email']})
        first=self.accounts.create_idempotent(service,'admin','invite-key',body)
        self.assertEqual(first['invitation_status'],'ALREADY_EXISTS_NO_RESEND')
        self.assertEqual(self.accounts.create_idempotent(service,'admin','invite-key',body),first)
        with self.assertRaises(HubError):self.accounts.create_idempotent(service,'admin','invite-key',{**body,'email':'other@example.com'})

class ExportCleanupTests(unittest.TestCase):
    def test_foreign_export_metadata_is_never_deleted(self):
        from cloudhub.driver_router import DriverRouter
        from unittest.mock import Mock
        router=DriverRouter({'s3_backup_bucket':'private','account_id':'123456789012'})
        row={'recovery_point':'rp-'+'a'*24,'owner_id':'alice','environment_id':'env-'+'b'*24,'template_id':'T01'}
        provider=client('s3');prefix='recovery/'+row['recovery_point']+'/exports/';key=prefix+'op-export.json'
        native=Mock();native.expire_backup.return_value=True
        with Stubber(provider) as stub,patch.object(router,'driver',return_value=native),patch('boto3.client',return_value=provider):
            stub.add_response('list_objects_v2',{'Contents':[{'Key':key}]},{'Bucket':'private','Prefix':prefix,'MaxKeys':100,'ExpectedBucketOwner':'123456789012'})
            stub.add_response('head_object',{'Metadata':{'cloudhub-owner':'bob'}},{'Bucket':'private','Key':key,'ExpectedBucketOwner':'123456789012'})
            with self.assertRaises(HubError):router.expire_backup(row)

class CostTests(unittest.TestCase):
    def setUp(self):
        self.client=client('ce');self.stub=Stubber(self.client);self.stub.activate()
        from decimal import Decimal
        self.env={'environment_id':'env-'+'a'*24,'created_at':Decimal(1767225600),'estimate':{'total_milliusd':Decimal(5000)}}
        self.now=1767484800
    def tearDown(self):self.stub.assert_no_pending_responses();self.stub.deactivate()
    def active_tag(self):
        self.stub.add_response('list_cost_allocation_tags',{'CostAllocationTags':[{'TagKey':'cloudhub:environment','Status':'Active','Type':'UserDefined'}]},
            {'TagKeys':['cloudhub:environment'],'Status':'Active','Type':'UserDefined','MaxResults':100})
    def expected(self):return {'TimePeriod':{'Start':'2026-01-01','End':'2026-01-04'},'Granularity':'DAILY','Metrics':['UnblendedCost'],
        'Filter':{'Tags':{'Key':'cloudhub:environment','Values':[self.env['environment_id']],'MatchOptions':['EQUALS']}},'GroupBy':[{'Type':'DIMENSION','Key':'SERVICE'}]}
    def test_cost_query_is_environment_scoped_paginated_and_provisional(self):
        self.active_tag()
        def page(amount,day):return {'ResultsByTime':[{'TimePeriod':{'Start':day,'End':'2026-01-04'},'Estimated':True,'Groups':[{'Keys':['EC2'],'Metrics':{'UnblendedCost':{'Amount':amount,'Unit':'USD'}}}]}]}
        first=page('1.2345','2026-01-01');first['NextPageToken']='next'
        self.stub.add_response('get_cost_and_usage',first,self.expected())
        self.stub.add_response('get_cost_and_usage',page('0.7655','2026-01-02'),{**self.expected(),'NextPageToken':'next'})
        result=ActualCosts(True,self.client).report(self.env,self.now)
        self.assertEqual(result['actual_milliusd'],2000);self.assertEqual(result['difference_milliusd'],-3000)
        self.assertEqual(result['status'],'PROVISIONAL');self.assertFalse(result['final'])
    def test_empty_cost_groups_never_become_zero_cost(self):
        self.active_tag();self.stub.add_response('get_cost_and_usage',{'ResultsByTime':[{'TimePeriod':{'Start':'2026-01-01','End':'2026-01-04'},'Groups':[],'Estimated':True}]},self.expected())
        result=ActualCosts(True,self.client).report(self.env,self.now)
        self.assertEqual(result['status'],'PENDING');self.assertIsNone(result['actual_milliusd'])
    def test_inactive_tag_never_queries_or_reports_account_total(self):
        self.stub.add_response('list_cost_allocation_tags',{'CostAllocationTags':[]},{'TagKeys':['cloudhub:environment'],'Status':'Active','Type':'UserDefined','MaxResults':100})
        result=ActualCosts(True,self.client).report(self.env,self.now)
        self.assertEqual(result['status'],'TAGS_INACTIVE');self.assertIsNone(result['actual_milliusd'])
    def test_disabled_cost_feature_makes_no_provider_call(self):
        self.assertEqual(ActualCosts(False,self.client).report(self.env,self.now)['status'],'NOT_CONFIGURED')

class NotificationTests(unittest.TestCase):
    def test_pending_email_is_not_reported_as_confirmed(self):
        provider=client('sns');topic='arn:aws:sns:us-east-1:123456789012:alerts'
        with Stubber(provider) as stub:
            stub.add_response('list_subscriptions_by_topic',{'Subscriptions':[{'SubscriptionArn':'PendingConfirmation','Endpoint':'admin@example.com','Protocol':'email','TopicArn':topic}]},{'TopicArn':topic})
            result=subscription_status(topic,'admin@example.com',provider)
            self.assertEqual(result['status'],'PENDING_CONFIRMATION');self.assertFalse(result['delivery_verified'])
    def test_confirmation_and_delivery_evidence_are_separate(self):
        provider=client('sns');topic='arn:aws:sns:us-east-1:123456789012:alerts'
        with Stubber(provider) as stub:
            stub.add_response('list_subscriptions_by_topic',{'Subscriptions':[{'SubscriptionArn':topic+':uuid','Endpoint':'admin@example.com','Protocol':'email','TopicArn':topic}]},{'TopicArn':topic})
            result=subscription_status(topic,'admin@example.com',provider)
            self.assertEqual(result['status'],'CONFIRMED');self.assertFalse(result['delivery_verified'])

class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.s=Service(MemoryStore(),clock=lambda:1000);self.rid='rp-'+'a'*24;self.oid='op-'+'b'*24;self.eid='env-'+'c'*24
        self.key='recovery/'+self.rid+'/exports/'+self.oid+'.json'
        rows=[{'pk':'R#'+self.rid,'owner_id':'alice','status':'AVAILABLE','recovery_point':self.rid,'environment_id':self.eid,'retain_until':1100},
              {'pk':'O#'+self.oid,'owner_id':'alice','action':'export_backup','status':'SUCCEEDED','operation_id':self.oid,'recovery_point':self.rid,
               'result':{'export_key':self.key,'filename':self.rid+'.json','label':'Tải bản sao'}}]
        self.s.store.commit(rows,{r['pk']:None for r in rows})
        self.settings={'s3_backup_bucket':'private','account_id':'123456789012'}
    def test_foreign_user_and_deleted_recovery_never_receive_url(self):
        with self.assertRaises(HubError):download(self.s,'bob',self.oid,self.settings,object())
        rp=self.s.store.get('R#'+self.rid);rp['status']='DELETED';self.s.save(rp)
        with self.assertRaises(HubError):download(self.s,'alice',self.oid,self.settings,object())
    def test_url_lifetime_is_bounded_by_retention_and_metadata_ownership(self):
        provider=client('s3')
        with Stubber(provider) as stub:
            stub.add_response('head_object',{'Metadata':{'cloudhub-owner':'alice','cloudhub-environment':self.eid,'cloudhub-recovery':self.rid}},
                {'Bucket':'private','Key':self.key,'ExpectedBucketOwner':'123456789012'})
            with patch.object(provider,'generate_presigned_url',return_value='https://signed.example/download') as sign:
                result=download(self.s,'alice',self.oid,self.settings,provider)
            self.assertEqual(result['expires_at'],1100);self.assertEqual(sign.call_args.kwargs['ExpiresIn'],100)
            self.assertNotIn('ExpectedBucketOwner',sign.call_args.kwargs['Params'])
    def test_foreign_object_metadata_is_rejected(self):
        provider=client('s3')
        with Stubber(provider) as stub:
            stub.add_response('head_object',{'Metadata':{'cloudhub-owner':'bob'}},{'Bucket':'private','Key':self.key,'ExpectedBucketOwner':'123456789012'})
            with self.assertRaises(HubError):download(self.s,'alice',self.oid,self.settings,provider)

if __name__=='__main__':unittest.main()
