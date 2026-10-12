"""Local behavior checks for cleanup coverage and deployed container file access."""
import base64,io,json,os,sys,threading,unittest,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from cloudhub.stack_driver import StackDriver
from test_processors import ENV,S,client
from botocore.stub import Stubber

class ReadinessTests(unittest.TestCase):
    def test_sdk_refresh_uses_regional_api_and_preserves_identity(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        from access_token import TokenProvider
        def token(sub,exp):return 'header.'+base64.urlsafe_b64encode(json.dumps({'sub':sub,'iss':'https://cognito-idp.ap-southeast-1.amazonaws.com/pool','client_id':'testclient','token_use':'access','exp':exp}).encode()).decode().rstrip('=')+'.signature'
        values={'CLOUDHUB_ACCESS_TOKEN':token('alice',1000),'CLOUDHUB_REFRESH_TOKEN':'local-test-refresh','CLOUDHUB_AUTH_METHOD':'api','CLOUDHUB_REGION':'ap-southeast-1','CLOUDHUB_COGNITO_DOMAIN':'https://sandbox.auth.ap-southeast-1.amazoncognito.com','CLOUDHUB_CLIENT_ID':'testclient'}
        with patch.dict(os.environ,values):provider=TokenProvider()
        after=token('alice',9999999999)
        with patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps({'AuthenticationResult':{'AccessToken':after}}).encode())) as request:
            self.assertEqual(provider.get(),after)
            self.assertEqual(request.call_args.args[0].full_url,'https://cognito-idp.ap-southeast-1.amazonaws.com/')
            self.assertEqual(json.loads(request.call_args.args[0].data)['AuthFlow'],'REFRESH_TOKEN_AUTH')
        provider.token=values['CLOUDHUB_ACCESS_TOKEN'];provider.region='us-east-1'
        with patch('urllib.request.urlopen') as request:
            with self.assertRaises(ValueError):provider.get()
            request.assert_not_called()
    def test_refresh_preserves_subject_and_never_sends_to_unapproved_domain(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        from access_token import TokenProvider
        def token(sub,exp):return 'header.'+base64.urlsafe_b64encode(json.dumps({'sub':sub,'iss':'issuer','exp':exp}).encode()).decode().rstrip('=')+'.signature'
        before=token('alice',1000);after=token('alice',9999999999)
        values={'CLOUDHUB_ACCESS_TOKEN':before,'CLOUDHUB_REFRESH_TOKEN':'local-test-refresh',
            'CLOUDHUB_COGNITO_DOMAIN':'https://sandbox.auth.ap-southeast-1.amazoncognito.com','CLOUDHUB_CLIENT_ID':'test-client'}
        with patch.dict(os.environ,values):provider=TokenProvider()
        with patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps({'access_token':after}).encode())) as request:
            self.assertEqual(provider.get(),after);self.assertEqual(request.call_args.args[0].full_url,values['CLOUDHUB_COGNITO_DOMAIN']+'/oauth2/token')
            self.assertEqual(provider.get(),after);self.assertEqual(request.call_count,1)
        provider.token=before;provider.domain='https://unapproved.example'
        with patch('urllib.request.urlopen') as request:
            with self.assertRaises(ValueError):provider.get()
            request.assert_not_called()

    def test_refresh_rejects_different_authenticated_subject(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        from access_token import TokenProvider
        def token(sub):return 'header.'+base64.urlsafe_b64encode(json.dumps({'sub':sub,'iss':'issuer','exp':1000}).encode()).decode().rstrip('=')+'.signature'
        values={'CLOUDHUB_ACCESS_TOKEN':token('alice'),'CLOUDHUB_REFRESH_TOKEN':'local-test-refresh',
            'CLOUDHUB_COGNITO_DOMAIN':'https://sandbox.auth.ap-southeast-1.amazoncognito.com','CLOUDHUB_CLIENT_ID':'test-client'}
        with patch.dict(os.environ,values):provider=TokenProvider()
        with patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps({'access_token':token('bob')}).encode())):
            with self.assertRaises(RuntimeError):provider.get()
        self.assertEqual(provider.token,values['CLOUDHUB_ACCESS_TOKEN'])
    def test_generated_service_enis_saved_by_exact_environment_group(self):
        ec2=client('ec2');stub=Stubber(ec2);driver=StackDriver(S,{'ec2':ec2})
        driver.assert_stack=lambda e:None
        env={**ENV,'template_id':'T06','resources':[{'type':'AWS::EC2::SecurityGroup','id':'sg-0123456789abcdef0'}]}
        stub.add_response('describe_network_interfaces',{'NetworkInterfaces':[{'NetworkInterfaceId':'eni-0123456789abcdef0'}]},
            {'Filters':[{'Name':'group-id','Values':['sg-0123456789abcdef0']}]})
        with stub:driver.inventory(env)
        self.assertIn('eni-0123456789abcdef0',[r['id'] for r in env['resources']]);stub.assert_no_pending_responses()

    def test_container_serves_registered_file_as_download_and_blocks_traversal(self):
        fake=type('S3',(),{'list_objects_v2':lambda *a,**kw:{'Contents':[]},
            'get_object':lambda *a,**kw:{'Body':io.BytesIO(b'<script>demo</script>')}})()
        with patch('boto3.client',return_value=fake):
            from cloudhub import container_app
        with patch.object(container_app,'s3',fake),patch.dict(os.environ,{'DATA_BUCKET':'registered-test'}):
            server=ThreadingHTTPServer(('127.0.0.1',0),container_app.Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                base='http://127.0.0.1:'+str(server.server_port)
                with urllib.request.urlopen(base+'/files/test.html') as response:
                    self.assertEqual(response.read(),b'<script>demo</script>');self.assertEqual(response.headers['Content-Disposition'],'attachment')
                    self.assertEqual(response.headers['Content-Type'],'application/octet-stream')
                with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(base+'/files/%2E%2E/secret.txt')
                self.assertEqual(error.exception.code,400)
            finally:server.shutdown();server.server_close();thread.join(timeout=2)

    def test_unapproved_draft_has_estimates_but_never_deployable(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        from prepare_review import prepare
        from test_processors import rates
        prices={**rates(),'approved':False,'source_urls':['https://example.invalid/local-only']}
        result=prepare({'account_id':'123456789012','region':'test-only','budget_usd':200,'validation_templates':['T09']},prices)
        self.assertFalse(result['deployable']);self.assertIn('T09',result['planning_only_estimates_1h']);self.assertTrue(result['not_deployed'])

    def test_redshift_batch_submission_uses_transaction_and_stable_token(self):
        from cloudhub.redshift_driver import RedshiftDriver
        import hashlib
        sdk=client('redshift-data');stub=Stubber(sdk);driver=RedshiftDriver(S,{'redshift-data':sdk});driver.scoped=lambda e:None
        env={**ENV,'warehouse_secret':'arn:aws:secretsmanager:ap-southeast-1:123456789012:secret:test','outputs':{'WorkgroupName':'cloudhub-env-wg'}}
        op={'operation_id':'op-'+'b'*24};sqls=['DELETE FROM records','SELECT 1']
        stub.add_response('batch_execute_statement',{'Id':'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'},
            {'WorkgroupName':env['outputs']['WorkgroupName'],'Database':'cloudhub','SecretArn':env['warehouse_secret'],
                'ClientToken':hashlib.sha256((op['operation_id']+'copy').encode()).hexdigest(),'Sqls':sqls})
        stub.add_response('describe_statement',{'Id':'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee','Status':'FINISHED'},{'Id':'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'})
        with stub:
            self.assertIsNone(driver.sql(env,op,'copy',sqls));self.assertTrue(driver.sql(env,op,'copy',sqls))
        stub.assert_no_pending_responses()
if __name__=='__main__':unittest.main()
