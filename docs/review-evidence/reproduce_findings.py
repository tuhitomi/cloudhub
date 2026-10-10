"""Historical reproductions for commit cf52ef3 (before R01–R06 were fixed).

Current behavior is covered by tests/test_review_fixes.py. This archived script
is expected to stop when run on fixed source; it is not a current validation suite.

Successful execution CONFIRMS current defects; it is not a passing acceptance suite.
"""
import contextlib,importlib,io,json,os,sys
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'tests')]
from cloudhub.service import Service
from cloudhub.store import MemoryStore
from cloudhub.cost import Estimator
from cloudhub.worker import Worker
from cloudhub.errors import HubError
from cloudhub import api
from cloudhub.aws_driver import AWSDriver
from test_domain import Driver,RATES

results=[]
def record(name,condition,**observed):
    assert condition,(name,observed)
    results.append({'finding':name,'confirmed_issue':True,'mode':'LOCAL_MOCK_OR_STATIC','observed':observed})
def fixture():
    store=MemoryStore();driver=Driver();service=Service(store,clock=lambda:1000,estimator=Estimator(RATES),verified={'T01'},validation={'T05'},budget_milliusd=100000)
    worker=Worker(service,driver);op=service.create('alice','create-key',{'template_id':'T01','duration_hours':1})
    for _ in range(10):
        if worker.tick(op['operation_id'])['done']:break
    return service,driver,worker,store.get('E#'+op['environment_id'])

# A final backup becomes deletable before the source retirement completes.
s,d,w,e=fixture();retire=s.command('alice',e['environment_id'],'destroy','retire-key');w.tick(retire['operation_id'])
rp=s.store.get('R#'+retire['recovery_point'])
assert rp['status']=='AVAILABLE' and s.store.get(e['pk'])['lock']==retire['operation_id']
delete=s.backup_command('alice',rp['recovery_point'],'delete_backup','delete-key',{})
d.expire_backup=lambda _:True;w.tick(delete['operation_id']);w.tick(retire['operation_id'])
record('R01_FINAL_BACKUP_DELETABLE_DURING_RETIREMENT','delete' in d.calls and s.store.get(rp['pk'])['status']=='DELETED',backup_state=s.store.get(rp['pk'])['status'],source_delete_called='delete' in d.calls,retirement_phase=s.store.get(retire['pk'])['phase'])

# A delete-phase retry also skips validation of its now-deleted final backup.
s,d,w,e=fixture();retire=s.command('alice',e['environment_id'],'destroy','retire-key');w.tick(retire['operation_id'])
with contextlib.redirect_stdout(io.StringIO()):w.fail(retire['operation_id'],'DELETE denied')
delete=s.backup_command('alice',retire['recovery_point'],'delete_backup','delete-key',{});d.expire_backup=lambda _:True;w.tick(delete['operation_id'])
retry=s.control_operation('alice',retire['operation_id'],'retry','retry-key',{});w.tick(retire['operation_id'],retry['generation'])
record('R01B_DELETE_RETRY_WITHOUT_FINAL_BACKUP','delete' in d.calls,backup_state=s.store.get('R#'+retire['recovery_point'])['status'],source_delete_called='delete' in d.calls)

# EBS recovery metadata can be incomplete after a timeout/lost response.
driver=AWSDriver.__new__(AWSDriver);driver.ec2=Mock();driver.aws_backup=Mock();driver.settings={'backup_vault':'review-vault'}
expired=driver.expire_backup({'recovery_point':'rp-review','owner_id':'alice','environment_id':'env-review','status':'FAILED','backup_job_id':'job-may-have-completed'})
record('R02_EBS_EXPIRY_WITHOUT_AWS_RECONCILIATION',expired and not driver.ec2.mock_calls and not driver.aws_backup.mock_calls,returned_deleted=expired,aws_calls=0)

# Removed admin membership is enforced only on /admin/*, not validation creates.
s,d,w,e=fixture();original_service,original_dispatch=api.SERVICE,api.DISPATCH;api.SERVICE=s;api.DISPATCH=None
event={'rawPath':'/environments','headers':{'Idempotency-Key':'stale-admin-key'},'requestContext':{'requestId':'review','http':{'method':'POST'},'authorizer':{'jwt':{'claims':{'sub':'former-admin','username':'former-admin','token_use':'access','cognito:groups':['admin']}}}},'body':json.dumps({'template_id':'T05','duration_hours':1,'validation_run':True})}
with patch('cloudhub.accounts.Accounts.assert_admin',side_effect=HubError('FORBIDDEN','removed admin',403)) as live_check:
    response=api.handler(event)
    record('R03_STALE_ADMIN_CAN_CREATE_UNVERIFIED_TEMPLATE',response['statusCode']==202 and live_check.call_count==0,http_status=response['statusCode'],live_admin_checks=live_check.call_count)
api.SERVICE,api.DISPATCH=original_service,original_dispatch

# Disabling access in U# does not apply to the separate T09 read API.
ddb=Mock()
def read_item(**args):
    key=args['Key']['pk']['S']
    return {'Item':{'enabled':{'BOOL':False}}} if key.startswith('U#') else {'Item':{'status':{'S':'READY'},'deadline':{'N':'9999999999'}}}
ddb.get_item.side_effect=read_item;ddb.scan.return_value={'Items':[{'row_id':{'S':'R#demo'},'text':{'S':'sample'}}]}
with patch('boto3.client',return_value=ddb),patch.dict(os.environ,{'OWNER':'alice','ENVIRONMENT':'env-review','MANAGEMENT':'management','DATA_TABLE':'data'}):
    module=importlib.import_module('cloudhub.data_api_app');module.ddb=ddb
    response=module.handler({'requestContext':{'authorizer':{'jwt':{'claims':{'sub':'alice','token_use':'access'}}}}})
    queried=[c.kwargs['Key']['pk']['S'] for c in ddb.get_item.call_args_list]
    record('R04_DISABLED_ACCOUNT_STILL_READS_T09',response['statusCode']==200 and 'U#alice' not in queried,http_status=response['statusCode'],queried_management_keys=queried)

# Residual resources are a successful workflow and emit no failure alarm metric.
s,d,w,e=fixture();d.residual=[{'type':'AWS::EC2::Volume','id':'vol-residual'}];retire=s.command('alice',e['environment_id'],'destroy','retire-key')
captured=io.StringIO()
with contextlib.redirect_stdout(captured):
    for _ in range(10):
        done=w.tick(retire['operation_id'])
        if done['done']:break
record('R05_RESIDUAL_HAS_NO_ALERT_EVENT',s.store.get(e['pk'])['status']=='RESIDUAL' and not done['failed'] and 'CloudHubOperationFailed' not in captured.getvalue(),environment_status=s.store.get(e['pk'])['status'],operation_status=s.store.get(retire['pk'])['status'],worker_failed=done['failed'],emitted_log=captured.getvalue())

template=json.loads((ROOT/'infra/control-plane.json').read_text(encoding='utf-8'))
role=template['Resources']['SessionRole']['Properties']
record('R06_SHARED_SSM_ROLE_HAS_NO_PARAMETER_BOUNDARY',any('AmazonSSMManagedInstanceCore' in json.dumps(p) for p in role.get('ManagedPolicyArns',[])) and not role.get('PermissionsBoundary') and not role.get('Policies'),managed_policy='AmazonSSMManagedInstanceCore',boundary_present=bool(role.get('PermissionsBoundary')),inline_deny_present=bool(role.get('Policies')),provider_policy_verified_from_official_docs=True)

out={'recorded_at':datetime.now(timezone.utc).isoformat(),'mode':'REVIEW_REPRODUCTIONS_NOT_ACCEPTANCE_TESTS','aws_mutations':0,'findings':results}
Path(__file__).with_name('reproduction-results.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(out,ensure_ascii=False,indent=2))
