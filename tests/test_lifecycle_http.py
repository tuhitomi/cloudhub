"""Retries cannot duplicate commands or hide authorization/throttling failures."""
import json,sys,unittest,urllib.error
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lifecycle_http import api_call

class Response:
    status=202
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self):return b'{"operation_id":"op-example"}'

class LifecycleHTTPTests(unittest.TestCase):
    def test_lost_post_response_reuses_exact_key_and_body_and_captures_real_status(self):
        requests=[];events=[]
        def send(request,timeout):
            requests.append(request)
            if len(requests)==1:raise urllib.error.URLError(ConnectionAbortedError('test'))
            return Response()
        with patch('lifecycle_http.urllib.request.urlopen',send):
            value,metric=api_call('https://example.invalid','/environments',{'template_id':'T02'},lambda:'synthetic',events)
        self.assertEqual(requests[0].get_header('Idempotency-key'),requests[1].get_header('Idempotency-key'))
        self.assertEqual(requests[0].data,requests[1].data)
        self.assertEqual(metric['http_status'],202);self.assertEqual(metric['transport_retries'],1)
        self.assertEqual(len(events),1);self.assertEqual(value['operation_id'],'op-example')
    def test_auth_and_throttling_errors_are_not_retried(self):
        for status in [401,403,404,429,500]:
            error=urllib.error.HTTPError('https://example.invalid',status,'test',{},None)
            with patch('lifecycle_http.urllib.request.urlopen',side_effect=error) as send:
                with self.assertRaises(urllib.error.HTTPError):api_call('https://example.invalid','/templates',None,lambda:'synthetic')
                self.assertEqual(send.call_count,1)
    def test_transport_failure_is_bounded_and_never_claims_success(self):
        events=[]
        with patch('lifecycle_http.urllib.request.urlopen',side_effect=urllib.error.URLError(TimeoutError('test'))) as send:
            with self.assertRaises(urllib.error.URLError):api_call('https://example.invalid','/templates',None,lambda:'synthetic',events)
            self.assertEqual(send.call_count,3);self.assertEqual(len(events),3)
    def test_certificate_failures_are_not_retried(self):
        import ssl
        with patch('lifecycle_http.urllib.request.urlopen',side_effect=urllib.error.URLError(ssl.SSLCertVerificationError('test'))) as send:
            with self.assertRaises(urllib.error.URLError):api_call('https://example.invalid','/templates',None,lambda:'synthetic')
            self.assertEqual(send.call_count,1)
