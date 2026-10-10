"""Product risk checks are LOCAL/MOCK; no live AWS results are asserted."""
import io,json,sys,unittest,zipfile
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from cloudhub.service import Service
from cloudhub.store import MemoryStore
from cloudhub.cost import Estimator
from cloudhub.errors import HubError
from cloudhub.worker import Worker
from cloudhub import api
from test_domain import RATES,Driver

class PortalTests(unittest.TestCase):
    def setUp(self):
        self.now=1000;self.store=MemoryStore();self.driver=Driver()
        self.s=Service(self.store,clock=lambda:self.now,estimator=Estimator(RATES),verified={'T01'},budget_milliusd=100000)
        self.worker=Worker(self.s,self.driver)
    def ready(self):
        op=self.s.create('alice','createkey',{'template_id':'T01','duration_hours':1})
        for _ in range(10):
            if self.worker.tick(op['operation_id'])['done']:break
        return self.store.get('E#'+op['environment_id'])
    def backup(self):
        env=self.ready();op=self.s.command('alice',env['environment_id'],'backup','backupkey')
        for _ in range(10):
            if self.worker.tick(op['operation_id'])['done']:break
        return env,self.store.get('R#'+op['recovery_point'])
    def test_remaining_limits_observe_user_and_global_reservations(self):
        self.s.create('alice','createkey',{'template_id':'T01','duration_hours':1})
        row=self.s.limits('alice');self.assertEqual(row['remaining_environments'],1)
        self.assertGreater(row['user_reserved_milliusd'],0)
        self.s.set_limits('admin','alice',{'max_active':1,'budget_milliusd':0})
        row=self.s.limits('alice');self.assertEqual(row['remaining_environments'],0);self.assertEqual(row['remaining_milliusd'],0)
        with self.assertRaises(HubError):self.s.create('alice','secondkey',{'template_id':'T01','duration_hours':1})
    def test_per_user_budget_is_enforced_in_create_transaction(self):
        self.s.set_limits('admin','alice',{'max_active':2,'budget_milliusd':0})
        with self.assertRaises(HubError) as error:self.s.create('alice','createkey',{'template_id':'T01','duration_hours':1})
        self.assertEqual(error.exception.code,'BUDGET_EXCEEDED');self.assertIsNone(self.store.get('Q#GLOBAL'))
    def test_renewal_invalidates_old_expiry_warning(self):
        env=self.ready();self.now=env['deadline']-600
        self.assertEqual(len(self.s.notifications('alice')['items']),1)
        self.assertEqual(self.s.notifications('bob')['items'],[])
        with ThreadPoolExecutor(max_workers=8) as pool:rows=list(pool.map(lambda _:self.s.remind(env['environment_id']),range(16)))
        self.assertEqual(sum(r is not None for r in rows),1)
        self.s.extend('alice',env['environment_id'],'extendkey',{'hours':1})
        self.assertEqual(self.s.notifications('alice')['items'],[])
        self.assertIsNone(self.s.remind(env['environment_id']))
    def failed_backup(self):
        env=self.ready();self.driver.fail_backup=True
        op=self.s.command('alice',env['environment_id'],'backup','backupkey')
        self.worker.tick(op['operation_id']);return env,self.store.get('O#'+op['operation_id'])
    def test_retry_reuses_environment_and_rejects_stale_workflow(self):
        env,op=self.failed_backup();self.driver.fail_backup=False
        after=self.s.control_operation('alice',op['operation_id'],'retry','retrykey',{})
        self.assertEqual(after['environment_id'],env['environment_id']);self.assertEqual(after['generation'],1)
        self.assertEqual(self.s.control_operation('alice',op['operation_id'],'retry','retrykey',{})['generation'],1)
        before=self.store.get(env['pk'])
        self.assertTrue(self.worker.tick(op['operation_id'],0)['done'])
        self.assertEqual(self.store.get(env['pk']),before)
        self.worker.fail(op['operation_id'],'old failure',0)
        self.assertEqual(self.store.get('O#'+op['operation_id'])['status'],'QUEUED')
        for _ in range(10):
            if self.worker.tick(op['operation_id'],1)['done']:break
        self.assertEqual(self.store.get(env['pk'])['status'],'READY')
        self.assertEqual(self.driver.calls.count('create'),1)
    def test_retry_waits_for_workflow_terminal_and_preserves_failed_state(self):
        _,op=self.failed_backup();self.s.workflow_terminal=lambda _:False
        with self.assertRaises(HubError) as error:self.s.control_operation('alice',op['operation_id'],'retry','retrykey',{})
        self.assertEqual(error.exception.code,'WORKFLOW_RUNNING')
        self.assertEqual(self.store.get(op['pk'])['status'],'FAILED')
    def test_backup_retry_uses_fresh_point_checkpoints_and_budget(self):
        _,old=self.failed_backup();rp=self.store.get('R#'+old['recovery_point']);rp['manifest_sha256']='old-source';self.s.save(rp)
        old.update(backup_cursor=32,backup_verify_cursor=32,backup_job_id='failed-native-job',efs_freeze_instance='failed-command',warehouse_manifest='old-statement');self.s.save(old)
        reserved=self.s.limits('alice')['user_reserved_milliusd']
        new=self.s.control_operation('alice',old['operation_id'],'retry','retrykey',{})
        self.assertNotEqual(new['recovery_point'],old['recovery_point'])
        fresh=self.store.get('R#'+new['recovery_point']);self.assertNotIn('manifest_sha256',fresh);self.assertEqual(fresh['retry_of'],old['recovery_point'])
        self.assertTrue({'backup_cursor','backup_verify_cursor','backup_job_id','efs_freeze_instance','warehouse_manifest'}.isdisjoint(new))
        self.assertGreater(self.s.limits('alice')['user_reserved_milliusd'],reserved)
    def test_successful_data_retry_returns_source_to_ready(self):
        env=self.ready();op=self.s.command('alice',env['environment_id'],'backup','backupkey')
        op['action']='write_file';op['phase']='WRITE_FILE';self.s.save(op);self.worker.fail(op['operation_id'],'SSM failed')
        self.s.control_operation('alice',op['operation_id'],'retry','retrykey',{});self.driver.write_file=lambda *_:None
        self.worker.tick(op['operation_id'],1)
        self.assertEqual(self.store.get(env['pk'])['status'],'READY');self.assertFalse(self.store.get(env['pk'])['needs_admin'])
    def test_sweeper_old_execution_failure_cannot_fail_new_retry(self):
        from cloudhub import runtime
        from unittest.mock import Mock
        _,old=self.failed_backup();self.s.control_operation('alice',old['operation_id'],'retry','retrykey',{})
        old['status']='RUNNING';original=self.store.scan_kind
        provider=Mock();provider.describe_execution.return_value={'status':'FAILED'}
        with patch.object(self.store,'scan_kind',side_effect=lambda kind:[old] if kind=='operation' else original(kind)),patch.object(runtime,'build_service',return_value=self.s),patch.object(runtime,'build_driver',return_value=self.driver),patch('boto3.client',return_value=provider),patch.dict('os.environ',{'STATE_MACHINE_ARN':'arn:aws:states:us-east-1:123456789012:stateMachine:test'}):
            runtime.sweep_handler({})
        self.assertEqual(self.store.get(old['pk'])['status'],'QUEUED')
    def test_dispatch_serializes_dynamo_generation_as_json_integer(self):
        from cloudhub import runtime
        from unittest.mock import Mock
        env=self.ready();op=self.store.list_owner('alice','operation')[0];op['generation']=Decimal(1);self.s.save(op)
        provider=Mock()
        with patch.object(runtime,'build_service',return_value=self.s),patch('boto3.client',return_value=provider),patch.dict('os.environ',{'STATE_MACHINE_ARN':'arn:aws:states:us-east-1:123456789012:stateMachine:test'}):runtime.dispatch(op['operation_id'])
        call=provider.start_execution.call_args.kwargs
        self.assertTrue(call['name'].endswith('-r1'));self.assertEqual(json.loads(call['input'])['generation'],1)
    def test_reminder_does_not_break_pending_schedule_version(self):
        from cloudhub import runtime
        env=self.ready();self.now=env['deadline']-600;env['schedule_pending']=True;self.s.save(env)
        with patch.object(runtime,'build_service',return_value=self.s),patch.object(runtime,'build_driver',return_value=self.driver):runtime.sweep_handler({})
        self.assertFalse(self.store.get(env['pk'])['schedule_pending'])
    def test_cancel_failed_task_never_deletes_or_releases_source(self):
        env,op=self.failed_backup();quota=deepcopy(self.store.get('Q#alice'))
        result=self.s.control_operation('alice',op['operation_id'],'cancel','cancelkey',{})
        self.assertEqual(result['status'],'CANCELLED');self.assertNotIn('delete',self.driver.calls)
        self.assertEqual(self.store.get('Q#alice'),quota);self.assertEqual(self.store.get(env['pk'])['status'],'ERROR_RETAINED')
        self.assertTrue(self.worker.tick(op['operation_id'])['done'])
        with self.assertRaises(HubError):self.s.control_operation('bob',op['operation_id'],'cancel','cancelkey',{})
    def test_active_task_cannot_be_cancelled_by_error_control(self):
        op=self.s.create('alice','createkey',{'template_id':'T01','duration_hours':1})
        with self.assertRaises(HubError):self.s.control_operation('alice',op['operation_id'],'cancel','cancelkey',{})
    def test_delete_backup_fences_restore_and_keeps_environment(self):
        env,rp=self.backup();before=self.store.get(env['pk']);quota=self.store.get('Q#alice')
        op=self.s.backup_command('alice',rp['recovery_point'],'delete_backup','deletekey',{})
        with self.assertRaises(HubError):self.s.create('alice','restorekey',{'template_id':'T01','duration_hours':1,'recovery_point':rp['recovery_point']})
        self.driver.expire_backup=lambda _:True
        self.worker.tick(op['operation_id'])
        self.assertEqual(self.store.get(rp['pk'])['status'],'DELETED')
        self.assertEqual(self.store.get(env['pk']),before);self.assertEqual(self.store.get('Q#alice'),quota)
    def test_restore_lock_blocks_delete_and_export(self):
        _,rp=self.backup()
        self.s.create('alice','restorekey',{'template_id':'T01','duration_hours':1,'recovery_point':rp['recovery_point']})
        for action in ['delete_backup','export_backup']:
            with self.assertRaises(HubError):self.s.backup_command('alice',rp['recovery_point'],action,'request_'+action,{})
    def test_simultaneous_export_and_delete_accept_only_one(self):
        _,rp=self.backup()
        def submit(action):
            try:return self.s.backup_command('alice',rp['recovery_point'],action,'request_'+action,{})
            except HubError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(submit,['export_backup','delete_backup']))
        self.assertEqual(sum(r is not None for r in rows),1)
    def test_backup_delete_failure_releases_lock_and_preserves_source(self):
        env,rp=self.backup();op=self.s.backup_command('alice',rp['recovery_point'],'delete_backup','deletekey',{})
        self.worker.fail(op['operation_id'],'provider denied')
        self.assertEqual(self.store.get(env['pk'])['status'],'READY')
        self.assertEqual(self.store.get(rp['pk'])['status'],'DELETE_FAILED')
        self.assertIsNone(self.store.get(rp['pk'])['delete_lock'])
    def test_cost_refresh_does_not_take_environment_lock_and_is_throttled(self):
        env=self.ready();op=self.s.cost_command('alice',env['environment_id'],'costkey__',{})
        self.assertIsNone(self.store.get(env['pk'])['lock'])
        with patch('cloudhub.actual_cost.ActualCosts.report',return_value={'status':'PENDING','actual_milliusd':None}):self.worker.tick(op['operation_id'])
        self.assertEqual(self.store.get(env['pk'])['status'],'READY')
        with self.assertRaises(HubError):self.s.cost_command('alice',env['environment_id'],'costkey2_',{})
    def test_disabled_account_is_blocked_even_with_unexpired_jwt(self):
        self.store.commit([{'pk':'U#alice','enabled':False}],{'U#alice':None})
        with patch.object(api,'SERVICE',self.s),patch.object(api,'DISPATCH',None):
            event={'rawPath':'/limits','requestContext':{'http':{'method':'GET'},'authorizer':{'jwt':{'claims':{'sub':'alice','token_use':'access'}}}}}
            self.assertEqual(api.handler(event)['statusCode'],403)
    def test_admin_retirement_keeps_owner_and_records_actor(self):
        env=self.ready();op=self.s.command('alice',env['environment_id'],'destroy','adminkey',admin_actor='admin',reason='Kết thúc phiên thử nghiệm')
        self.assertEqual(op['owner_id'],'alice');self.assertEqual(op['admin_actor'],'admin');self.assertEqual(op['phase'],'BACKUP')

class ExportTests(unittest.TestCase):
    def setUp(self):
        self.rid='rp-'+'a'*24;self.eid='env-'+'b'*24
        self.env={'owner_id':'alice','environment_id':self.eid,'template_id':'T02'}
        self.rp={'owner_id':'alice','environment_id':self.eid,'recovery_point':self.rid,'template_id':'T02','created_at':Decimal(100),'retain_until':Decimal(2000),'scope':'files','status':'AVAILABLE'}
        self.op={'operation_id':'op-'+'c'*24}
        self.uploads=[]
        class S3:
            def put_object(inner,**kwargs):self.uploads.append(kwargs)
        self.s3=S3()
    def test_file_export_contains_verified_bytes_and_manifest(self):
        from cloudhub.backup_export import BackupExports
        from cloudhub.s3_files import sha
        content=b'hello';files=type('Files',(),{'s3':self.s3,'_manifest':lambda _,rp,owner:[{'key':'folder/test.txt','size':5,'sha256':sha(content)}],
            '_prefix':lambda _,rp:'recovery/'+self.rid+'/', '_bytes':lambda *_:(content,{})})()
        router=type('Router',(),{'driver':lambda _,rp:files})()
        result=BackupExports({'s3_backup_bucket':'private','account_id':'123456789012'},router).export(self.env,self.op,self.rp)
        self.assertTrue(result['contains_all_protected_data'])
        with zipfile.ZipFile(io.BytesIO(self.uploads[0]['Body'])) as archive:
            self.assertEqual(archive.read('data/folder/test.txt'),content)
            self.assertEqual(json.loads(archive.read('cloudhub-backup.json'))['recovery_point'],self.rid)
    def test_native_export_explicitly_contains_metadata_only(self):
        from cloudhub.backup_export import BackupExports
        self.rp.update(template_id='T01',snapshot_id='snap-example')
        driver=type('Driver',(),{'s3':self.s3})();router=type('Router',(),{'driver':lambda _,rp:driver})()
        result=BackupExports({'s3_backup_bucket':'private','account_id':'123456789012'},router).export(self.env,self.op,self.rp)
        self.assertFalse(result['contains_all_protected_data'])
        self.assertFalse(json.loads(self.uploads[0]['Body'])['contains_snapshot_data'])
    def test_export_corruption_never_uploads_a_download(self):
        from cloudhub.backup_export import BackupExports
        files=type('Files',(),{'s3':self.s3,'_manifest':lambda *_:[{'key':'test.txt','size':5,'sha256':'0'*64}],
            '_prefix':lambda *_:'recovery/'+self.rid+'/', '_bytes':lambda *_:(b'hello',{})})()
        router=type('Router',(),{'driver':lambda _,rp:files})()
        with self.assertRaises(HubError):BackupExports({'s3_backup_bucket':'private','account_id':'123456789012'},router).export(self.env,self.op,self.rp)
        self.assertEqual(self.uploads,[])

if __name__=='__main__':unittest.main()
