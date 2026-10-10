"""T03/T04 demo process. PostgreSQL stores data; mutations only from localhost via SSM.

Bundled into EC2 user data; secrets fetched with an environment-specific instance role.
"""
import hashlib
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import boto3
import psycopg

def connect():
    # Fetch on each connection so RDS-managed password rotation continues to work.
    secret=json.loads(boto3.client('secretsmanager',region_name=os.environ['AWS_REGION']).get_secret_value(
        SecretId=os.environ['DB_SECRET'])['SecretString'])
    return psycopg.connect(host=os.environ['DB_HOST'],port=5432,user=secret['username'],password=secret['password'],
        dbname='cloudhub',sslmode='verify-full',sslrootcert='/opt/cloudhub/rds-ca.pem',connect_timeout=5)

def rows():
    with connect() as conn:
        return [{'record_id':key,'text':text} for key,text in conn.execute('SELECT record_id, value FROM records ORDER BY record_id').fetchall()]

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def respond(self,status,value,typ='application/json'):
        body=(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode() if typ=='application/json' else value.encode())
        self.send_response(status);self.send_header('Content-Type',typ+'; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_GET(self):
        try:
            values=rows()
            if self.path=='/health':return self.respond(200,{'ready':True})
            if self.path.startswith('/internal/'):
                if self.client_address[0] not in {'127.0.0.1','::1'}:return self.respond(403,{'error':'internal'})
                if self.path=='/internal/manifest':
                    encoded=json.dumps(values,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
                    return self.respond(200,{'sha256':hashlib.sha256(encoded).hexdigest(),'count':len(values)})
                if self.path=='/internal/records':return self.respond(200,{'items':values})
            if self.path=='/':return self.respond(200,'<!doctype html><html lang="vi"><meta charset="utf-8"><h1>CloudHub web và PostgreSQL</h1><p>'+str(len(values))+' bản ghi. Quản lý dữ liệu qua CloudHub.</p></html>','text/html')
            self.respond(404,{'error':'not found'})
        except Exception:self.respond(503,{'error':'database not ready'})
    def do_POST(self):
        if self.path!='/internal/records' or self.client_address[0] not in {'127.0.0.1','::1'}:return self.respond(403,{'error':'internal'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=4096:return self.respond(400,{'error':'payload'})
            value=json.loads(self.rfile.read(size))
            # Service validates IDs/text before SSM; also validate at this boundary.
            import re
            if set(value)!={'record_id','text','operation_id'} or not re.fullmatch('[A-Za-z0-9_-]{1,64}',value['record_id']) or not isinstance(value['text'],str) or len(value['text'].encode())>256 or any(ord(c)<32 and c not in '\n\t' for c in value['text']):return self.respond(400,{'error':'payload'})
            with connect() as conn:
                conn.execute('LOCK TABLE records IN EXCLUSIVE MODE')
                old=conn.execute('SELECT payload FROM commands WHERE operation_id=%s',(value['operation_id'],)).fetchone()
                payload=json.dumps(value,sort_keys=True)
                if old:
                    if old[0]!=payload:return self.respond(409,{'error':'idempotency'})
                else:
                    exists=conn.execute('SELECT 1 FROM records WHERE record_id=%s',(value['record_id'],)).fetchone()
                    if not exists and conn.execute('SELECT count(*) FROM records').fetchone()[0]>=32:return self.respond(409,{'error':'record quota'})
                    conn.execute('INSERT INTO records(record_id,value) VALUES(%s,%s) ON CONFLICT(record_id) DO UPDATE SET value=excluded.value',(value['record_id'],value['text']))
                    conn.execute('INSERT INTO commands(operation_id,payload) VALUES(%s,%s)',(value['operation_id'],payload))
            self.respond(200,{'record_id':value['record_id']})
        except Exception:self.respond(503,{'error':'database unavailable'})

def main():
    with connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS records (record_id varchar(64) PRIMARY KEY, value varchar(1024) NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS commands (operation_id varchar(64) PRIMARY KEY, payload text NOT NULL)')
        conn.execute("INSERT INTO records VALUES ('demo','CloudHub PostgreSQL demo') ON CONFLICT DO NOTHING")
    ThreadingHTTPServer(('0.0.0.0',8080),Handler).serve_forever()
if __name__=='__main__':main()
