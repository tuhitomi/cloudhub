"""Local simulations and SDK shape checks; zero AWS lifecycle evidence."""
import base64
import hashlib
import io
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from cloudhub import api
from cloudhub.cost import Estimator
from cloudhub.errors import HubError
from cloudhub.service import Service
from cloudhub.store import MemoryStore
from cloudhub.worker import Worker
from cloudhub.s3_driver import S3Driver
from cloudhub.s3_files import OwnedFiles, assert_bucket, bucket_name, validate_file
from cloudhub.s3_template import s3_template

RATES = {'approved':True,'region':'test-region','updated_at':'2026-10-09T00:00:00Z','units':{
    's3_gib_month':'0.03','s3_requests_1000':'0.01','cloudfront_transfer_gib':'0.1',
    'cloudfront_requests_10000':'0.01','logs_gib':'0.5','control_plane_session':'0.02'}}
SETTINGS = {'account_id':'123456789012','region':'us-east-1','s3_backup_bucket':'shared-backups',
            'cf_role_arn':'arn:aws:iam::123456789012:role/cf'}

class AwsError(Exception):
    def __init__(self, code):
        self.response={'Error':{'Code':code}}
        super().__init__(code)

class Pages:
    def __init__(self, client, method): self.client,self.method=client,method
    def paginate(self, **kw): return [getattr(self.client,self.method)(**kw)]

class FakeS3:
    def __init__(self):
        self.buckets={SETTINGS['s3_backup_bucket']:{'tags':{},'files':{}}}
        self.calls=[];self.serial=0;self.lose_put=False;self.delete_errors=False
    def _bucket(self, name):
        if name not in self.buckets: raise AwsError('NoSuchBucket')
        return self.buckets[name]
    def get_paginator(self, method):return Pages(self, method)
    def get_bucket_tagging(self, **kw):
        self.calls.append(('get_bucket_tagging',kw))
        return {'TagSet':[{'Key':k,'Value':v} for k,v in self._bucket(kw['Bucket'])['tags'].items()]}
    def list_objects_v2(self, **kw):
        self.calls.append(('list_objects_v2',kw))
        rows=[{'Key':k,'Size':len(v[-1]['data'])} for k,v in self._bucket(kw['Bucket'])['files'].items() if not v[-1].get('marker') and k.startswith(kw.get('Prefix',''))]
        return {'Contents':sorted(rows,key=lambda v:v['Key'])[:kw.get('MaxKeys',1000)]}
    def get_object(self, **kw):
        self.calls.append(('get_object',kw))
        versions=self._bucket(kw['Bucket'])['files'].get(kw['Key'],[])
        found=next((v for v in versions if v['id']==kw.get('VersionId')),None) if kw.get('VersionId') else (versions[-1] if versions else None)
        if not found or found.get('marker'):raise AwsError('NoSuchKey')
        return {'Body':io.BytesIO(found['data']),'VersionId':found['id'],'ContentType':found['type'],'Metadata':found['meta']}
    def head_object(self, **kw):
        self.calls.append(('head_object',kw))
        versions=self._bucket(kw['Bucket'])['files'].get(kw['Key'],[])
        if not versions or versions[-1].get('marker'):raise AwsError('404')
        item=versions[-1]
        return {'VersionId':item['id'],'ETag':'"'+hashlib.md5(item['data']).hexdigest()+'"','ContentType':item['type'],'Metadata':item['meta']}
    def put_object(self, **kw):
        self.calls.append(('put_object',deepcopy(kw)))
        versions=self._bucket(kw['Bucket'])['files'].setdefault(kw['Key'],[])
        if kw.get('IfNoneMatch')=='*' and versions:raise AwsError('PreconditionFailed')
        if 'IfMatch' in kw and (not versions or '"'+hashlib.md5(versions[-1]['data']).hexdigest()+'"'!=kw['IfMatch']):raise AwsError('PreconditionFailed')
        self.serial+=1;vid=str(self.serial)
        versions.append({'id':vid,'data':kw['Body'],'type':kw['ContentType'],'meta':kw.get('Metadata',{})})
        if self.lose_put:self.lose_put=False;raise RuntimeError('simulated lost HTTP response')
        return {'VersionId':vid}
    def list_object_versions(self, **kw):
        self.calls.append(('list_object_versions',kw))
        rows=[{'Key':key,'VersionId':item['id'],'marker':item.get('marker',False)} for key,items in self._bucket(kw['Bucket'])['files'].items() for item in items][:kw.get('MaxKeys',1000)]
        return {'Versions':[{k:v for k,v in item.items() if k!='marker'} for item in rows if not item['marker']],
                'DeleteMarkers':[{k:v for k,v in item.items() if k!='marker'} for item in rows if item['marker']]}
    def delete_objects(self, **kw):
        self.calls.append(('delete_objects',deepcopy(kw)))
        if self.delete_errors:return {'Errors':[{'Key':'demo.txt','Code':'AccessDenied'}]}
        files=self._bucket(kw['Bucket'])['files']
        for item in kw['Delete']['Objects']:
            key=item['Key']
            if 'VersionId' in item:
                files[key]=[v for v in files.get(key,[]) if v['id']!=item['VersionId']]
                if not files[key]:files.pop(key,None)
            else:files.pop(key,None)
        return {}
    def get_bucket_policy(self, **kw):
        self._bucket(kw['Bucket']);return {'Policy':'{}'}

class FakeCF:
    def __init__(self, s3):self.s3=s3;self.stacks={};self.deleted=[]
    def describe_stacks(self, StackName):
        if StackName not in self.stacks:
            error=AwsError('ValidationError');error.args=('does not exist',);raise error
        return {'Stacks':[deepcopy(self.stacks[StackName])]}
    def create_stack(self, **kw):
        template=json.loads(kw['TemplateBody']);r=template['Resources'];name=r['DataBucket']['Properties']['BucketName']
        tags={v['Key']:v['Value'] for v in r['DataBucket']['Properties']['Tags']}
        self.s3.buckets[name]={'tags':tags,'files':{}}
        self.stacks[kw['StackName']]={'StackStatus':'CREATE_COMPLETE','Tags':kw['Tags'],
            'Outputs':[{'OutputKey':k,'OutputValue':v} for k,v in {'BucketName':name,'DistributionId':'DIST'+tags['cloudhub:environment'][4:], 'WebsiteUrl':'https://d123.cloudfront.net'}.items()]}
    def get_paginator(self, method):return Pages(self,method)
    def list_stack_resources(self, StackName):
        outputs={v['OutputKey']:v['OutputValue'] for v in self.stacks[StackName]['Outputs']}
        return {'StackResourceSummaries':[{'LogicalResourceId':lid,'ResourceType':typ,'PhysicalResourceId':rid} for lid,typ,rid in [
            ('DataBucket','AWS::S3::Bucket',outputs['BucketName']),('Distribution','AWS::CloudFront::Distribution',outputs['DistributionId']),
            ('OriginAccess','AWS::CloudFront::OriginAccessControl','OAC'+outputs['DistributionId']),('BucketPolicy','AWS::S3::BucketPolicy',outputs['BucketName'])]]}
    def delete_stack(self, **kw):
        self.deleted.append(kw)
        stack=self.stacks[kw['StackName']];stack['StackStatus']='DELETE_COMPLETE'
        bucket=next(v['OutputValue'] for v in stack['Outputs'] if v['OutputKey']=='BucketName')
        if self.s3.buckets[bucket]['files']:raise AssertionError('Versioned bucket must be drained first')
        del self.s3.buckets[bucket]

class FakeCloudFront:
    def __init__(self, cf):self.cf=cf
    def get_distribution(self, Id):
        for stack in self.cf.stacks.values():
            outputs={v['OutputKey']:v['OutputValue'] for v in stack['Outputs']}
            if outputs['DistributionId']==Id and stack['StackStatus']!='DELETE_COMPLETE':
                return {'Distribution':{'Status':'Deployed','DistributionConfig':{'Enabled':True},'DomainName':'d123.cloudfront.net'}}
        raise AwsError('NoSuchDistribution')
    def get_origin_access_control(self, Id):
        self.get_distribution(Id=Id[3:]);return {}

class S3Tests(unittest.TestCase):
    def setUp(self):
        self.now=1000;self.s3=FakeS3();self.cf=FakeCF(self.s3)
        self.s=Service(MemoryStore(),clock=lambda:self.now,estimator=Estimator(RATES),verified={'T02'},budget_milliusd=100000)
        self.driver=S3Driver(SETTINGS,clients={'s3':self.s3,'cloudformation':self.cf,'cloudfront':FakeCloudFront(self.cf),'scheduler':object()},clock=lambda:self.now,
            http_get=lambda url:(200,self.s3.buckets[self.current_bucket]['files']['.cloudhub/ready.txt'][-1]['data'] if '.cloudhub/ready.txt' in url else b'html'))
        self.driver.schedule=lambda env:None
        self.w=Worker(self.s,self.driver)
    def run_op(self,op):
        for _ in range(200):
            self.current_bucket=bucket_name({'environment_id':op['environment_id']},SETTINGS)
            result=self.w.tick(op['operation_id'])
            if result['done']:return result
        self.fail('nonterminating local workflow')
    def ready(self,key='create_first',rp=None):
        body={'template_id':'T02','duration_hours':1}
        if rp:body['recovery_point']=rp
        op=self.s.create('alice',key,body);result=self.run_op(op)
        return self.s.store.get('E#'+op['environment_id']),result
    def payload(self,data=b'cycle fixture',key='cycle.txt'):
        return {'key':key,'content_base64':base64.b64encode(data).decode(),'content_type':'text/plain'}
    def write(self,env,key='write_first',data=b'cycle fixture'):
        op=self.s.write_file('alice',env['environment_id'],key,self.payload(data));self.run_op(op);return op
    def test_complete_local_lifecycle_uses_latest_files_and_verifies_restore(self):
        env,result=self.ready();self.assertFalse(result['failed'])
        self.write(env);self.write(env,'write_second',b'updated fixture')
        backup=self.s.command('alice',env['environment_id'],'backup','backup_first');self.run_op(backup)
        rp=self.s.owned('R',backup['recovery_point'],'alice');self.assertEqual(rp['status'],'AVAILABLE');self.assertFalse(rp['restore_verified'])
        self.assertEqual(len([v for v in rp['files'] if v['key']=='cycle.txt']),1)
        destroy=self.s.command('alice',env['environment_id'],'destroy','destroy_first');self.run_op(destroy)
        removed=self.s.owned('E',env['environment_id'],'alice');self.assertEqual(removed['status'],'DELETED')
        self.assertTrue({'AWS::S3::Bucket','AWS::S3::BucketPolicy'} <= {v['type'] for v in removed['cleanup_report']['deleted']})
        self.assertTrue(removed['cleanup_report']['retained']);self.assertFalse(removed['cleanup_report']['residual'])
        restored,result=self.ready('restore_first',rp['recovery_point']);self.assertFalse(result['failed'])
        self.assertEqual(self.s3.buckets[restored['bucket_name']]['files']['cycle.txt'][-1]['data'],b'updated fixture')
        self.assertTrue(self.s.owned('R',rp['recovery_point'],'alice')['restore_verified'])
        final=self.s.command('alice',restored['environment_id'],'destroy','destroy_restored');self.run_op(final)
        self.assertEqual(self.s.owned('E',restored['environment_id'],'alice')['status'],'DELETED')
        self.assertEqual(self.s.store.get('Q#GLOBAL')['active'],0)
        self.assertIn(SETTINGS['s3_backup_bucket'],self.s3.buckets)
    def test_lost_write_response_reuses_same_version(self):
        env,_=self.ready();op=self.s.write_file('alice',env['environment_id'],'lost_write',self.payload())
        self.s3.lose_put=True
        with self.assertRaises(RuntimeError):self.w.tick(op['operation_id'])
        self.run_op(op)
        self.assertEqual(len(self.s3.buckets[env['bucket_name']]['files']['cycle.txt']),1)
        self.write(env,'same_bytes')
        self.assertEqual(len(self.s3.buckets[env['bucket_name']]['files']['cycle.txt']),1)
    def test_owner_and_backup_lock_prevent_file_writes(self):
        env,_=self.ready()
        with self.assertRaises(HubError) as error:self.s.write_file('bob',env['environment_id'],'cross_owner',self.payload())
        self.assertEqual(error.exception.status,404)
        self.s.command('alice',env['environment_id'],'backup','backup_lock')
        with self.assertRaises(HubError) as error:self.s.write_file('alice',env['environment_id'],'locked_write',self.payload())
        self.assertEqual(error.exception.code,'OPERATION_LOCKED')
        before=len(self.s3.calls)
        with self.assertRaises(HubError):OwnedFiles(self.s,SETTINGS,self.s3).list('bob',env['environment_id'])
        self.assertEqual(len(self.s3.calls),before)
    def test_file_input_blocks_path_escape_size_and_mime(self):
        for body in [self.payload(key='../index.html'),self.payload(key='.cloudhub/ready.txt'),self.payload(b'x'*8193),{**self.payload(),'content_type':'application/x-msdownload'}, {**self.payload(),'content_base64':'%%%'}]:
            with self.assertRaises(HubError):validate_file(body)
    def test_file_write_idempotency_and_limit_are_durable(self):
        env,_=self.ready();op=self.s.write_file('alice',env['environment_id'],'same_request',self.payload())
        self.assertEqual(op['operation_id'],self.s.write_file('alice',env['environment_id'],'same_request',self.payload())['operation_id'])
        with self.assertRaises(HubError):self.s.write_file('alice',env['environment_id'],'same_request',self.payload(b'changed'))
        self.run_op(op);row=self.s.owned('E',env['environment_id'],'alice');row['file_writes']=128;self.s.save(row)
        with self.assertRaises(HubError) as error:self.s.write_file('alice',env['environment_id'],'over_limit',self.payload())
        self.assertEqual(error.exception.code,'FILE_QUOTA')
    def test_corrupt_backup_blocks_source_deletion(self):
        env,_=self.ready();self.write(env)
        destroy=self.s.command('alice',env['environment_id'],'destroy','bad_backup')
        original=self.driver._immutable
        def corrupt(bucket,key,data,typ,meta):
            original(bucket,key,data,typ,meta)
            if bucket==SETTINGS['s3_backup_bucket'] and '/data/' in key:self.s3.buckets[bucket]['files'][key][-1]['data']=b'corrupted'
        self.driver._immutable=corrupt
        result=self.run_op(destroy)
        self.assertTrue(result['failed']);self.assertFalse(self.cf.deleted)
        self.assertIn(env['bucket_name'],self.s3.buckets)
        self.assertEqual(self.s.owned('E',env['environment_id'],'alice')['status'],'ERROR_RETAINED')
    def test_corrupt_restore_is_not_marked_verified(self):
        env,_=self.ready();backup=self.s.command('alice',env['environment_id'],'backup','restore_bad_backup');self.run_op(backup)
        rp=self.s.owned('R',backup['recovery_point'],'alice');prefix=rp['backup_prefix']
        self.s3.buckets[SETTINGS['s3_backup_bucket']]['files'][prefix+'data/demo.txt'][-1]['data']=b'corrupted'
        restored,result=self.ready('restore_corrupt',rp['recovery_point'])
        self.assertTrue(result['failed']);self.assertEqual(restored['status'],'CREATE_FAILED')
        self.assertFalse(self.s.owned('R',rp['recovery_point'],'alice')['restore_verified'])
    def test_versioned_cleanup_removes_delete_markers_and_handles_partial_failure(self):
        env,_=self.ready();self.write(env)
        self.s3.buckets[env['bucket_name']]['files']['old.txt']=[{'id':'marker-1','marker':True}]
        destroy=self.s.command('alice',env['environment_id'],'destroy','delete_markers');self.run_op(destroy)
        deleted=[call for method,call in self.s3.calls if method=='delete_objects' and call['Bucket']==env['bucket_name']]
        self.assertTrue(any({'Key':'old.txt','VersionId':'marker-1'} in c['Delete']['Objects'] for c in deleted))
        env,_=self.ready('create_next');self.s3.delete_errors=True
        destroy=self.s.command('alice',env['environment_id'],'destroy','partial_delete');self.assertTrue(self.run_op(destroy)['failed'])
        self.assertIn(env['bucket_name'],self.s3.buckets)
    def test_missing_owner_tags_prevent_delete_and_report_unknown(self):
        env,_=self.ready();self.s3.buckets[env['bucket_name']]['tags']['cloudhub:owner']='bob'
        with self.assertRaises(HubError):self.driver.delete(env,{'recovery_point':None})
        self.assertFalse(self.cf.deleted)
        report=self.driver.audit(env,[]);self.assertTrue(report['unknown'])
    def test_recovery_expiry_deletes_only_exact_prefix(self):
        env,_=self.ready();first=self.s.command('alice',env['environment_id'],'backup','backup_expire1');self.run_op(first)
        second=self.s.command('alice',env['environment_id'],'backup','backup_expire2');self.run_op(second)
        rp=self.s.owned('R',first['recovery_point'],'alice');other=self.s.owned('R',second['recovery_point'],'alice')
        self.assertFalse(self.driver.expire_backup(rp));self.assertTrue(self.driver.expire_backup(rp))
        self.assertTrue(any(k.startswith(other['backup_prefix']) for k in self.s3.buckets[SETTINGS['s3_backup_bucket']]['files']))
        self.assertIn(env['bucket_name'],self.s3.buckets)
    def test_lost_create_metadata_discovers_source_before_cleanup(self):
        env,_=self.ready();row=self.s.owned('E',env['environment_id'],'alice');row['status']='CREATE_FAILED';row.pop('bucket_name');row['resources']=[];self.s.save(row)
        destroy=self.s.command('alice',env['environment_id'],'destroy','discover_source')
        self.assertEqual(destroy['phase'],'DELETE');self.assertTrue(self.run_op(destroy)['failed'])
        self.assertFalse(self.cf.deleted)
        # Retry uses newly persisted bucket metadata and now takes a final backup.
        retry=self.s.command('alice',env['environment_id'],'destroy','discover_retry');self.run_op(retry)
        self.assertEqual(self.s.owned('E',env['environment_id'],'alice')['status'],'DELETED')
    def test_s3_prices_are_required_without_ec2_units(self):
        quote=Estimator(RATES).estimate('T02',1);self.assertTrue(quote['approved']);self.assertNotIn('compute',quote['lines'])
        bad=deepcopy(RATES);del bad['units']['cloudfront_requests_10000']
        self.assertFalse(Estimator(bad).estimate('T02',1)['approved'])
        self.assertFalse(Estimator(RATES).estimate('T01',1)['approved'])
        with self.assertRaises(HubError):self.s.create('alice','wrong_config',{'template_id':'T02','duration_hours':1,'configuration':{'instance_type':'t3.micro','root_gib':16,'data_gib':8}})
    def test_api_file_command_returns_202_without_payload_and_enforces_gate(self):
        env,_=self.ready();old=api.SERVICE,api.DISPATCH;api.SERVICE=self.s;api.DISPATCH=lambda oid:None
        event={'requestContext':{'authorizer':{'jwt':{'claims':{'sub':'alice','token_use':'access'}}},'http':{'method':'POST'}},
            'rawPath':'/environments/'+env['environment_id']+'/files','headers':{'Idempotency-Key':'api_file_request'},'body':json.dumps(self.payload())}
        try:
            response=api.handler(event);self.assertEqual(response['statusCode'],202);self.assertNotIn('file',json.loads(response['body']))
            self.s.verified=set()
            with self.assertRaises(HubError) as error:self.s.create('alice','unverified_t02',{'template_id':'T02','duration_hours':1})
            self.assertEqual(error.exception.code,'TEMPLATE_UNVERIFIED')
        finally:api.SERVICE,api.DISPATCH=old
    def test_s3_template_keeps_origin_private_and_backup_outside_environment(self):
        template=s3_template({'environment_id':'env-'+'a'*24,'owner_id':'alice','template_id':'T02'},SETTINGS)
        resources=template['Resources'];bucket=resources['DataBucket']['Properties']
        self.assertEqual(bucket['VersioningConfiguration']['Status'],'Enabled')
        self.assertTrue(all(bucket['PublicAccessBlockConfiguration'].values()))
        self.assertNotIn('WebsiteConfiguration',bucket);self.assertNotIn('S3BackupBucket',resources)
        cf=resources['Distribution']['Properties']['DistributionConfig']
        self.assertEqual(cf['DefaultCacheBehavior']['CachePolicyId'],'4135ea2d-6df8-44a3-9df3-4b5a84be39ad')
    def test_unreachable_website_does_not_start_deadline(self):
        self.driver.http_get=lambda url:(403,b'Forbidden')
        op=self.s.create('alice','not_ready_http',{'template_id':'T02','duration_hours':1})
        for _ in range(5):
            self.current_bucket=bucket_name({'environment_id':op['environment_id']},SETTINGS);self.w.tick(op['operation_id'])
        env=self.s.owned('E',op['environment_id'],'alice')
        self.assertEqual(env['status'],'PROVISIONING');self.assertIsNone(env['deadline'])
    def test_file_write_finishes_at_expiry_and_hands_off_final_backup(self):
        env,_=self.ready();op=self.s.write_file('alice',env['environment_id'],'write_at_expiry',self.payload())
        self.now=env['deadline']+1
        result=self.w.tick(op['operation_id'])
        self.assertTrue(result['done']);self.assertTrue(result['expiry_operation_id'])
        expiry=self.s.owned('O',result['expiry_operation_id'],'alice')
        self.assertEqual(expiry['phase'],'BACKUP');self.assertEqual(expiry['action'],'destroy')

if __name__=='__main__':unittest.main()
