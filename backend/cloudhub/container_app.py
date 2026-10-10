"""Approved T06 image contract: read registered S3 data, expose demo pages only."""
import html,json,os,re
from urllib.parse import unquote,urlsplit
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import boto3
from botocore.config import Config
s3=boto3.client('s3',config=Config(connect_timeout=1,read_timeout=3,retries={'total_max_attempts':1}))
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            objects=s3.list_objects_v2(Bucket=os.environ['DATA_BUCKET'],MaxKeys=34).get('Contents',[])
            if self.path=='/health':data=b'ok';typ='text/plain'
            elif self.path=='/':
                keys=[v['Key'] for v in objects if not v['Key'].startswith('.cloudhub/')]
                data=('<!doctype html><html lang="vi"><meta charset="utf-8"><title>CloudHub Docker</title><h1>Ứng dụng Docker</h1><p>Dữ liệu dùng từ S3:</p><ul>'+''.join('<li>'+html.escape(k)+'</li>' for k in keys)+'</ul></html>').encode();typ='text/html; charset=utf-8'
            elif self.path=='/demo' or urlsplit(self.path).path.startswith('/files/'):
                key='demo.txt' if self.path=='/demo' else unquote(urlsplit(self.path).path[7:])
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,127}',key) or any(p in {'','.','..'} for p in key.split('/')):
                    self.send_error(400);return
                response=s3.get_object(Bucket=os.environ['DATA_BUCKET'],Key=key)
                with response['Body'] as body:data=body.read(8193)
                if len(data)>8192:raise ValueError('limit')
                typ='application/octet-stream' if self.path!='/demo' else 'text/plain; charset=utf-8'
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',typ);self.send_header('X-Content-Type-Options','nosniff')
            if typ=='application/octet-stream':self.send_header('Content-Disposition','attachment')
            self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
        except Exception:self.send_error(503,'Registered data unavailable')
    def log_message(self,*args):pass
if __name__=='__main__':ThreadingHTTPServer(('0.0.0.0',8080),Handler).serve_forever()
