"""Live AWS evidence harness for all ten processors; never substitutes mock evidence."""
import argparse
import base64
import hashlib
import json
import os
import time
import uuid
import urllib.request
import urllib.error
import subprocess,tempfile,csv,io
import re
from datetime import datetime,timezone
from pathlib import Path
from access_token import TokenProvider

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--api',required=True);p.add_argument('--config',required=True);p.add_argument('--out',required=True)
    p.add_argument('--template',choices=['T'+str(i).zfill(2) for i in range(1,11)],default='T01');p.add_argument('--cycles',type=int,default=2)
    p.add_argument('--approved-create-resources',action='store_true')
    args=p.parse_args()
    if not args.api.startswith('https://'):raise SystemExit('Live AWS API must use HTTPS')
    config=json.loads(Path(args.config).read_text(encoding='utf-8'))
    if not args.approved_create_resources or config.get('acknowledged') is not True or config.get('preflight_passed') is not True or config.get('budget_usd',0)<=0:
        raise SystemExit('Requires approved account, region, budget, deployment permissions and explicit resource-creation flag.')
    if args.template not in config.get('validation_templates',[]):raise SystemExit('Template is not approved for validation')
    if args.cycles<2:raise SystemExit('Need at least two independent lifecycle cycles')
    import boto3
    session=boto3.Session(profile_name=config.get('profile'),region_name=config['region'])
    identity=session.client('sts').get_caller_identity()
    if identity['Account']!=config['account_id']:raise SystemExit('AWS account mismatch')
    ssm,ec2=session.client('ssm'),session.client('ec2')
    access,other_access=TokenProvider(),TokenProvider('CLOUDHUB_OTHER_')
    if access.token==other_access.token:raise SystemExit('Ownership verification requires a distinct Cognito user')
    evidence={'mode':'AWS_REAL','started_at':datetime.now(timezone.utc).isoformat(),'account_id':identity['Account'],'region':config['region'],
              'template_id':args.template,'cycles':[],'completed_cycles':0,'warnings':['Retained snapshots/S3 copies remain billable until their 7-day policy expires.']}
    def call(path,body=None):
        headers={'Authorization':'Bearer '+access.get()}
        if body is not None:headers.update({'Content-Type':'application/json','Idempotency-Key':str(uuid.uuid4())})
        request=urllib.request.Request(args.api.rstrip('/')+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
        start=time.perf_counter()
        with urllib.request.urlopen(request,timeout=10) as r:
            payload=json.loads(r.read());status=r.status
        return payload,{'path':path,'http_status':status,'latency_ms':(time.perf_counter()-start)*1000}
    def wait(op):
        end=time.monotonic()+7300
        while time.monotonic()<end:
            current,_=call('/operations/'+op['operation_id'])
            if current['status']=='SUCCEEDED':return current
            if current['status']=='FAILED':raise RuntimeError(current.get('error','operation failed'))
            time.sleep(10)
        raise TimeoutError('Operation timed out; inspect retained source and workflow. No blind deletion.')
    def shell(env,script):
        inst=ec2.describe_instances(InstanceIds=[env['instance_id']])['Reservations'][0]['Instances'][0]
        tags={t['Key']:t['Value'] for t in inst.get('Tags',[])}
        if tags.get('cloudhub:environment')!=env['environment_id'] or tags.get('cloudhub:owner')!=env['owner_id']:
            raise RuntimeError('Refuse SSM command outside environment ownership')
        command=ssm.send_command(InstanceIds=[env['instance_id']],DocumentName='AWS-RunShellScript',Parameters={'commands':[script]},TimeoutSeconds=120)['Command']['CommandId']
        for _ in range(60):
            try:r=ssm.get_command_invocation(CommandId=command,InstanceId=env['instance_id'])
            except ssm.exceptions.InvocationDoesNotExist:time.sleep(2);continue
            if r['Status']=='Success':return {'command_id':command,'stdout':r.get('StandardOutputContent','').strip()}
            if r['Status'] not in ['Pending','InProgress','Delayed']:raise RuntimeError('SSM '+r['Status'])
            time.sleep(2)
        raise TimeoutError('SSM timeout')
    def read_http(url,auth=False):
        request=urllib.request.Request(url,headers={'Authorization':'Bearer '+access.get()} if auth else {})
        with urllib.request.urlopen(request,timeout=15) as response:
            if response.status!=200:raise RuntimeError('Access failed')
            data=response.read(65537)
            if len(data)>65536:raise ValueError('Unexpected response size')
            return data
    def ownership_checks(env):
        url=args.api.rstrip('/')+'/environments/'+env['environment_id']
        for credentials,status in [(None,401),(other_access.get(),404)]:
            request=urllib.request.Request(url,headers={'Authorization':'Bearer '+credentials} if credentials else {})
            try:
                with urllib.request.urlopen(request,timeout=10):raise RuntimeError('Unauthenticated or cross-owner access accepted')
            except urllib.error.HTTPError as exc:
                if exc.code!=status:raise RuntimeError('Expected authorization denial '+str(status)+', got '+str(exc.code))
        if args.template=='T09':
            request=urllib.request.Request(env['access_address'],headers={'Authorization':'Bearer '+other_access.get()})
            try:
                with urllib.request.urlopen(request,timeout=10):raise RuntimeError('Cross-owner data API accepted')
            except urllib.error.HTTPError as exc:
                if exc.code!=404:raise RuntimeError('T09 must hide another owner resource with 404')
        return {'unauthenticated':401,'different_owner':404}
    def ssh_verify(env):
        key,_=call('/environments/'+env['environment_id']+'/ssh-key')
        with tempfile.TemporaryDirectory(prefix='cloudhub-ssh-') as directory:
            if os.name=='nt':
                # Restrict the directory before writing a private key. OpenSSH on
                # Windows checks the DACL rather than the POSIX mode bits.
                identity=subprocess.run(['whoami','/user','/fo','csv','/nh'],check=True,capture_output=True,text=True)
                sid=next(csv.reader(io.StringIO(identity.stdout)))[1]
                if not re.fullmatch(r'S-1-\d+(?:-\d+)+',sid):raise RuntimeError('Cannot determine the current Windows user SID')
                subprocess.run(['icacls',directory,'/inheritance:r','/grant:r','*'+sid+':(OI)(CI)F'],check=True,capture_output=True,text=True)
            path=Path(directory)/'session.pem'
            fd=os.open(str(path),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as out:out.write(key['private_key'])
            result=subprocess.run(['ssh','-i',str(path),'-o','BatchMode=yes','-o','ConnectTimeout=15','-o','StrictHostKeyChecking=accept-new',
                '-o','UserKnownHostsFile='+str(Path(directory)/'known_hosts'),'ec2-user@'+env['public_ip'],'sha256sum /data/cycle.txt'],capture_output=True,text=True,timeout=30)
            if result.returncode:raise RuntimeError('SSH access failed; approve this client CIDR and inspect readiness')
            return result.stdout.strip()
    def data_read(env,cycle):
        op,metric=call('/environments/'+env['environment_id']+('/query_demo' if args.template=='T10' else '/list_records'),{})
        cycle['commands'].append(metric);value=wait(op)['result']
        if args.template=='T08' and len(value.get('verified_on_nodes',[]))!=2:raise RuntimeError('EFS not verified on both nodes')
        if args.template in {'T03','T04'}:read_http(env['access_address']+'/health')
        if args.template=='T09':
            payload=json.loads(read_http(env['access_address'],True))
            if payload['items']!=value['items']:raise RuntimeError('Authenticated data API mismatch')
        return value['items']
    def file_read(env,cycle):
        if args.template=='T07':
            share,_=call('/environments/'+env['environment_id']+'/share',{'key':'cycle.txt'})
            cycle.setdefault('share_expiries',[]).append(share['expires_at']);return read_http(share['url'])
        return read_http(env['access_address']+('/files/' if args.template=='T06' else '/')+'cycle.txt')
    try:
        for number in range(1,args.cycles+1):
            cycle={'number':number,'commands':[]};evidence['cycles'].append(cycle)
            body={'template_id':args.template,'duration_hours':1,'validation_run':True}
            create,metric=call('/environments',body);cycle['commands'].append(metric);cycle['create_operation']=create;wait(create)
            env,_=call('/environments/'+create['environment_id']);cycle['source_environment']=env
            cycle['authorization']=ownership_checks(env)
            fixture=uuid.uuid4().hex
            if args.template in {'T02','T06','T07'}:
                write,metric=call('/environments/'+env['environment_id']+'/files',{'key':'cycle.txt','content_base64':base64.b64encode((fixture+'\n').encode()).decode(),'content_type':'text/plain'})
                cycle['commands'].append(metric);cycle['file_operation']=wait(write)
                data=file_read(env,cycle)
                if data!=(fixture+'\n').encode():raise RuntimeError('Website write/access mismatch')
                cycle['write_and_verify']={'sha256':hashlib.sha256(data).hexdigest()}
            elif args.template in {'T03','T04','T08','T09','T10'}:
                item={'record_id':'cycle_'+str(number),'text':fixture}
                write,metric=call('/environments/'+env['environment_id']+('/load_dataset' if args.template=='T10' else '/put_record'),item)
                cycle['commands'].append(metric);cycle['data_write']=wait(write)
                if item not in data_read(env,cycle):raise RuntimeError('Written data not readable')
                cycle['write_and_verify']={'record':item}
            else:
                cycle['write_and_verify']=shell(env,"set -eu; printf '%s\\n' '"+fixture+"' > /data/cycle.txt; sync; sha256sum /data/cycle.txt")
                if ssh_verify(env)!=cycle['write_and_verify']['stdout']:raise RuntimeError('SSH and SSM data differ')
                cycle['ssh_access_verified']=True
            if args.template=='T05':
                with urllib.request.urlopen(env['access_address'],timeout=10) as response:cycle['public_http_status']=response.status;response.read()
            backup,metric=call('/environments/'+env['environment_id']+'/backup',{});cycle['commands'].append(metric);cycle['backup_operation']=wait(backup)
            destroy,metric=call('/environments/'+env['environment_id']+'/destroy',{});cycle['commands'].append(metric);cycle['destroy_operation']=wait(destroy)
            removed,_=call('/environments/'+env['environment_id']);cycle['source_cleanup']=removed['cleanup_report']
            if removed['status']!='DELETED':raise RuntimeError('Source has residual/unknown resources; resolve before claiming success')
            body['recovery_point']=backup['recovery_point'];restore,metric=call('/environments',body);cycle['commands'].append(metric);cycle['restore_operation']=wait(restore)
            restored,_=call('/environments/'+restore['environment_id']);cycle['restored_environment']=restored
            if args.template in {'T02','T06','T07'}:
                data=file_read(restored,cycle)
                cycle['restored_checksum']={'sha256':hashlib.sha256(data).hexdigest()}
            elif args.template in {'T03','T04','T08','T09','T10'}:
                rows=data_read(restored,cycle)
                if cycle['write_and_verify']['record'] not in rows:raise RuntimeError('Restored records differ')
                cycle['restored_checksum']={'record':cycle['write_and_verify']['record']}
            else:
                cycle['restored_checksum']=shell(restored,'set -eu; sha256sum /data/cycle.txt')
                if ssh_verify(restored)!=cycle['restored_checksum']['stdout']:raise RuntimeError('Restored SSH access mismatch')
            comparison='sha256' if args.template in {'T02','T06','T07'} else 'stdout' if args.template in {'T01','T05'} else 'record'
            if cycle['restored_checksum'][comparison]!=cycle['write_and_verify'][comparison]:raise RuntimeError('Restored checksum mismatch')
            rp,_=call('/recovery-points/'+backup['recovery_point']);cycle['recovery_point']=rp
            if not rp['restore_verified']:raise RuntimeError('Backend did not record data verification')
            cleanup,metric=call('/environments/'+restored['environment_id']+'/destroy',{});cycle['commands'].append(metric);cycle['restored_cleanup_operation']=wait(cleanup)
            final,_=call('/environments/'+restored['environment_id']);cycle['restored_cleanup']=final['cleanup_report']
            if final['status']!='DELETED':raise RuntimeError('Restored test resources remain')
            cycle['result']='PASS';evidence['completed_cycles']+=1
        evidence['long_command_under_2s']=all(m['http_status']==202 and m['latency_ms']<2000 for c in evidence['cycles'] for m in c['commands'])
        evidence['result']='PASS'
    except Exception as exc:
        evidence['result']='FAILED';evidence['error']=type(exc).__name__+': '+str(exc)
        evidence['warnings'].append('Failure keeps source data. Inspect all environment IDs recorded here, admin attention and AWS inventory; do not bypass final backup.')
        raise
    finally:
        evidence['finished_at']=datetime.now(timezone.utc).isoformat()
        out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
        print('Evidence saved to',out)

if __name__=='__main__':main()
