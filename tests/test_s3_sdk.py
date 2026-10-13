"""Botocore Stubber enforces real SDK request shapes; no network or AWS credentials."""
import io
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
try:
    import boto3
    from botocore.stub import Stubber
except ImportError:
    boto3=None
from cloudhub.s3_driver import S3Driver
from cloudhub.s3_files import bucket_name, sha
from cloudhub.errors import HubError
from test_s3 import SETTINGS

@unittest.skipUnless(boto3,'Install requirements-dev.txt for SDK verification')
class S3SDKTests(unittest.TestCase):
    def setUp(self):
        self.client=boto3.client('s3',region_name='us-east-1',aws_access_key_id='testing',aws_secret_access_key='testing')
        self.driver=S3Driver(SETTINGS,clients={'s3':self.client,'cloudformation':object(),'cloudfront':object(),'scheduler':object()})
        self.driver.cancel_schedule=lambda env:None
        self.env={'environment_id':'env-'+'a'*24,'owner_id':'alice','template_id':'T02'}
        self.bucket=bucket_name(self.env,SETTINGS);self.account=SETTINGS['account_id']
        self.tags={'TagSet':[{'Key':k,'Value':v} for k,v in {'cloudhub:environment':self.env['environment_id'],'cloudhub:owner':'alice','cloudhub:managed':'true'}.items()]}
    def tag(self,stub):stub.add_response('get_bucket_tagging',self.tags,{'Bucket':self.bucket,'ExpectedBucketOwner':self.account})
    def test_new_file_conditional_write_and_readback(self):
        op={'operation_id':'op-'+'b'*24,'file':{'key':'cycle.txt','content_base64':'YWJj','content_type':'text/plain'}}
        stub=Stubber(self.client);self.tag(stub)
        stub.add_client_error('head_object',service_error_code='404',http_status_code=404,expected_params={'Bucket':self.bucket,'Key':'cycle.txt','ExpectedBucketOwner':self.account})
        stub.add_response('list_objects_v2',{}, {'Bucket':self.bucket,'ExpectedBucketOwner':self.account})
        stub.add_response('put_object',{'VersionId':'version-1'}, {'Bucket':self.bucket,'Key':'cycle.txt','Body':b'abc','ContentType':'text/plain',
            'Metadata':{'cloudhub-operation':op['operation_id'],'sha256':sha(b'abc')},'ServerSideEncryption':'AES256','ExpectedBucketOwner':self.account,'IfNoneMatch':'*'})
        stub.add_response('get_object',{'Body':io.BytesIO(b'abc'),'VersionId':'version-1','ContentType':'text/plain'}, {'Bucket':self.bucket,'Key':'cycle.txt','ExpectedBucketOwner':self.account})
        with stub:self.driver.write_file(self.env,op)
        stub.assert_no_pending_responses();self.assertEqual(op['file_sha256'],sha(b'abc'))
    def test_retry_after_success_does_not_put_another_version(self):
        op={'operation_id':'op-'+'b'*24,'file':{'key':'cycle.txt','content_base64':'YWJj','content_type':'text/plain'}}
        stub=Stubber(self.client);self.tag(stub)
        params={'Bucket':self.bucket,'Key':'cycle.txt','ExpectedBucketOwner':self.account}
        stub.add_response('head_object',{'Metadata':{'cloudhub-operation':op['operation_id']},'ETag':'"etag"','ContentType':'text/plain'},params)
        stub.add_response('get_object',{'Body':io.BytesIO(b'abc'),'ContentType':'text/plain'},params)
        with stub:self.driver.write_file(self.env,op)
        stub.assert_no_pending_responses()
    def test_delete_targets_versions_and_delete_markers(self):
        stub=Stubber(self.client);self.tag(stub)
        stub.add_response('list_object_versions',{'Versions':[{'Key':'index.html','VersionId':'v1'}],
            'DeleteMarkers':[{'Key':'old.txt','VersionId':'marker1'}]}, {'Bucket':self.bucket,'MaxKeys':500,'ExpectedBucketOwner':self.account})
        stub.add_response('delete_objects',{}, {'Bucket':self.bucket,'ExpectedBucketOwner':self.account,'Delete':{
            'Objects':[{'Key':'index.html','VersionId':'v1'},{'Key':'old.txt','VersionId':'marker1'}],'Quiet':True}})
        with stub:self.assertFalse(self.driver.delete(self.env,{'recovery_point':'rp-'+'c'*24,'backup_cursor':1}))
        stub.assert_no_pending_responses()
    def test_foreign_bucket_tags_reject_before_any_delete(self):
        stub=Stubber(self.client);tags={'TagSet':[{'Key':'cloudhub:owner','Value':'bob'}]}
        stub.add_response('get_bucket_tagging',tags,{'Bucket':self.bucket,'ExpectedBucketOwner':self.account})
        with stub:
            with self.assertRaises(HubError) as error:self.driver.delete(self.env,{'recovery_point':None})
            self.assertEqual(error.exception.code,'RESOURCE_SCOPE_MISMATCH')
        stub.assert_no_pending_responses()
    def test_expiry_uses_exact_recovery_prefix(self):
        rp={'recovery_point':'rp-'+'c'*24};prefix='recovery/'+rp['recovery_point']+'/'
        stub=Stubber(self.client);key=prefix+'manifest.json'
        stub.add_response('list_objects_v2',{'Contents':[{'Key':key}]}, {'Bucket':SETTINGS['s3_backup_bucket'],'Prefix':prefix,'MaxKeys':100,'ExpectedBucketOwner':self.account})
        stub.add_response('delete_objects',{}, {'Bucket':SETTINGS['s3_backup_bucket'],'Delete':{'Objects':[{'Key':key}],'Quiet':True},'ExpectedBucketOwner':self.account})
        with stub:self.assertFalse(self.driver.expire_backup(rp))
        stub.assert_no_pending_responses()
    def test_full_cycle_after_dynamodb_number_deserialization(self):
        from boto3.dynamodb.types import TypeSerializer, TypeDeserializer
        from cloudhub.store import MemoryStore
        from test_s3 import S3Tests
        serializer,deserializer=TypeSerializer(),TypeDeserializer()
        class NumericStore(MemoryStore):
            def get(self,key):
                row=super().get(key)
                return deserializer.deserialize(serializer.serialize(row)) if row else None
        scenario=S3Tests('test_complete_local_lifecycle_uses_latest_files_and_verifies_restore')
        scenario.setUp();scenario.s.store=NumericStore()
        scenario.test_complete_local_lifecycle_uses_latest_files_and_verifies_restore()

if __name__=='__main__':unittest.main()
