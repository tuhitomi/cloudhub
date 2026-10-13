"""Risk tests only. These are LOCAL MOCK results, never AWS evidence."""
import json,os
from unittest.mock import patch
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from cloudhub import api
from cloudhub.service import Service
from cloudhub.store import MemoryStore
from cloudhub.cost import Estimator
from cloudhub.errors import HubError
from cloudhub.worker import Worker

RATES = {"approved": True, "region": "test-region", "updated_at": "2026-10-09T00:00:00Z", "units": {
    "ec2_hour": "0.02", "ebs_gib_month": "0.1", "ipv4_hour": "0.005", "snapshot_gib_month": "0.05",
    "transfer_gib": "0.09", "logs_gib": "0.5", "control_plane_session": "0.02"}}

class Driver:
    """Intentional fake: call history proves safety decisions without AWS."""
    def __init__(self): self.calls=[]; self.fail_backup=False; self.residual=[]
    def create(self,e,o,r): self.calls.append('create')
    def created(self,e): e.update(instance_id='i-mock',data_volume_id='vol-mock'); return True
    def ready(self,e,o,r): self.calls.append('restore_data_check' if r else 'ready'); return True
    def schedule(self,e): self.calls.append('schedule')
    def cancel_schedule(self,e): self.calls.append('cancel_schedule')
    def backup(self,e,o,r):
        self.calls.append('backup')
        if self.fail_backup: raise HubError('BACKUP_FAILED','mock backup failure',409)
        r.update(snapshot_id='snap-mock',manifest_sha256='mock-sha256');return True
    def delete(self,e,o):self.calls.append('delete');return True
    def resume(self,e):self.calls.append('resume')
    def audit(self,e,r):self.calls.append('audit');return {'deleted':[],'retained':r,'residual':self.residual,'unknown':[],'billable':r}

class DomainTests(unittest.TestCase):
    def setUp(self):
        self.now=1000;self.store=MemoryStore();self.driver=Driver()
        self.s=Service(self.store,clock=lambda:self.now,estimator=Estimator(RATES),verified={'T01'},
                       validation={'T05'},user_limit=2,total_limit=4,budget_milliusd=100000)
        self.worker=Worker(self.s,self.driver)
    def create(self,owner='alice',key='abcdefgh'):
        return self.s.create(owner,key,{'template_id':'T01','duration_hours':1})
    def run_op(self,op):
        for _ in range(15):
            result=self.worker.tick(op['operation_id'])
            if result['done']:return result
        self.fail('operation did not terminate')
    def ready(self):
        op=self.create();self.run_op(op);return self.store.get('E#'+op['environment_id'])
    def test_owner_cannot_spoof_or_cross_read(self):
        op=self.create()
        for kind,eid in [('E',op['environment_id']),('O',op['operation_id'])]:
            with self.assertRaises(HubError) as ctx:self.s.owned(kind,eid,'bob')
            self.assertEqual(ctx.exception.status,404)
        with self.assertRaises(HubError):self.s.create('alice','bad_owner',{'template_id':'T01','duration_hours':1,'owner_id':'bob'})
    def test_idempotent_concurrent_create(self):
        with ThreadPoolExecutor(max_workers=20) as pool:results=list(pool.map(lambda _:self.create(),range(40)))
        self.assertEqual(len({r['operation_id'] for r in results}),1)
        self.assertEqual(self.store.get('Q#alice')['active'],1)
    def test_idempotency_rejects_changed_payload(self):
        self.create()
        with self.assertRaises(HubError) as ctx:self.s.create('alice','abcdefgh',{'template_id':'T01','duration_hours':2})
        self.assertEqual(ctx.exception.code,'IDEMPOTENCY_MISMATCH')
    def test_simultaneous_quota_never_exceeds_user_limit(self):
        def submit(i):
            try:return self.create(key='request_'+str(i))
            except HubError:return None
        with ThreadPoolExecutor(max_workers=20) as pool:rows=list(pool.map(submit,range(30)))
        self.assertEqual(sum(r is not None for r in rows),2)
        self.assertEqual(self.store.get('Q#GLOBAL')['active'],2)
    def test_global_quota_and_budget(self):
        self.s.total_limit=1;self.create()
        with self.assertRaises(HubError) as ctx:self.create('bob','bobkey__')
        self.assertEqual(ctx.exception.code,'QUOTA_EXCEEDED')
        self.s.total_limit=4;self.s.budget=0
        with self.assertRaises(HubError) as ctx:self.create('bob','bobkey__')
        self.assertEqual(ctx.exception.code,'BUDGET_EXCEEDED')
    def test_session_starts_at_readiness_not_request(self):
        op=self.create();self.now=1600;self.run_op(op)
        env=self.store.get('E#'+op['environment_id'])
        self.assertEqual(env['deadline'],5200);self.assertEqual(env['ready_at'],1600)
    def test_completed_operation_is_noop(self):
        op=self.create();self.run_op(op);n=len(self.driver.calls);self.run_op(op)
        self.assertEqual(len(self.driver.calls),n)
    def test_unverified_template_gate_and_validation_admin(self):
        body={'template_id':'T05','duration_hours':1,'validation_run':True}
        with self.assertRaises(HubError):self.s.create('alice','validkey',body)
        self.assertEqual(self.s.create('admin','validkey',body,True)['action'],'create')
    def test_expiry_ignores_old_schedule_after_extension(self):
        env=self.ready();self.now=env['deadline']-1
        extension=self.s.extend('alice',env['environment_id'],'extendkey',{'hours':1})
        self.now=env['deadline'];self.assertIsNone(self.s.expire(env['environment_id']))
        self.assertEqual(extension['deadline'],env['deadline']+3600)
    def test_expiration_wins_transaction_and_blocks_extension(self):
        env=self.ready();self.now=env['deadline'];op=self.s.expire(env['environment_id'])
        self.assertEqual(op['action'],'destroy')
        with self.assertRaises(HubError):self.s.extend('alice',env['environment_id'],'extendkey',{'hours':1})
    def test_backup_failure_retains_source_and_alerts(self):
        env=self.ready();self.driver.fail_backup=True
        op=self.s.command('alice',env['environment_id'],'destroy','destroykey')
        self.assertTrue(self.run_op(op)['failed'])
        self.assertNotIn('delete',self.driver.calls)
        after=self.store.get(env['pk']);self.assertEqual(after['status'],'ERROR_RETAINED');self.assertTrue(after['needs_admin'])
        self.assertEqual(self.store.get('Q#alice')['active'],1)
    def test_lock_and_backup_ownership(self):
        env=self.ready();op=self.s.command('alice',env['environment_id'],'backup','backupkey')
        with self.assertRaises(HubError):self.s.command('alice',env['environment_id'],'destroy','destroykey')
        with self.assertRaises(HubError):self.s.owned('R',op['recovery_point'],'bob')
    def test_cleanup_residual_keeps_quota(self):
        env=self.ready();self.driver.residual=[{'type':'AWS::EC2::Volume','id':'vol-residual'}]
        op=self.s.command('alice',env['environment_id'],'destroy','destroykey');self.run_op(op)
        self.assertEqual(self.store.get(env['pk'])['status'],'RESIDUAL')
        self.assertEqual(self.store.get('Q#alice')['active'],1)
    def test_lost_create_response_cannot_bypass_backup_on_discovered_source(self):
        op=self.create();env=self.store.get('E#'+op['environment_id'])
        create_op=self.store.get('O#'+op['operation_id']);env['status']='CREATE_FAILED';env['lock']=None
        create_op['status']='FAILED';self.s.save(env,create_op)
        destroy=self.s.command('alice',env['environment_id'],'destroy','destroykey')
        self.assertEqual(destroy['phase'],'DELETE')
        def discover(row):row.update(instance_id='i-hidden',data_volume_id='vol-hidden')
        self.driver.inventory=discover
        result=self.run_op(destroy)
        self.assertTrue(result['failed']);self.assertNotIn('delete',self.driver.calls)
        self.assertEqual(self.store.get(env['pk'])['data_volume_id'],'vol-hidden')
    def test_clean_cleanup_releases_quota_once(self):
        env=self.ready();op=self.s.command('alice',env['environment_id'],'destroy','destroykey');self.run_op(op);self.run_op(op)
        self.assertEqual(self.store.get('Q#alice')['active'],0)
        self.assertGreater(self.store.get('Q#GLOBAL')['reserved_milliusd'],0)
    def test_restore_creates_new_environment_and_verifies_data(self):
        env=self.ready();op=self.s.command('alice',env['environment_id'],'backup','backupkey');self.run_op(op)
        restore=self.s.create('alice','restorekey',{'template_id':'T01','duration_hours':1,'recovery_point':op['recovery_point']})
        self.assertNotEqual(restore['environment_id'],env['environment_id']);self.run_op(restore)
        self.assertIn('restore_data_check',self.driver.calls)
        self.assertTrue(self.store.get('R#'+op['recovery_point'])['restore_verified'])
    def test_restore_denies_foreign_expired_and_concurrent_backup(self):
        env=self.ready();op=self.s.command('alice',env['environment_id'],'backup','backupkey');self.run_op(op)
        body={'template_id':'T01','duration_hours':1,'recovery_point':op['recovery_point']}
        with self.assertRaises(HubError):self.s.create('bob','restorekey',body)
        restore=self.s.create('alice','restorekey',body)
        with self.assertRaises(HubError):self.s.create('alice','restore2_',body)
        self.run_op(restore);self.now+=8*86400
        with self.assertRaises(HubError):self.s.create('alice','expired_',body)
    def test_unknown_price_blocks_create(self):
        self.s.estimator=Estimator()
        with self.assertRaises(HubError) as ctx:self.create()
        self.assertEqual(ctx.exception.code,'PRICES_UNAPPROVED')
    def test_quota_can_be_released_after_budget_or_limits_lowered(self):
        env=self.ready();op=self.s.command('alice',env['environment_id'],'destroy','destroykey')
        self.s.budget=0;self.s.total_limit=0;self.s.user_limit=0
        self.run_op(op);self.assertEqual(self.store.get('Q#alice')['active'],0)
    def test_expiry_records_activation_while_backup_holds_lock(self):
        env=self.ready();op=self.s.command('alice',env['environment_id'],'backup','backupkey')
        self.now=env['deadline'];self.assertIsNone(self.s.expire(env['environment_id']))
        self.assertEqual(self.store.get(env['pk'])['expiry_triggered_at'],self.now)
        result=self.run_op(op)
        self.assertIsNotNone(result['expiry_operation_id'])
        self.assertEqual(self.store.get(env['pk'])['status'],'RETIRING')
    def test_stale_expiry_read_is_fenced_by_new_deadline(self):
        env=self.ready();self.now=env['deadline'];original=self.store.commit;changed=[False]
        def interleaved(rows,expected):
            if not changed[0] and any(row.get('auto_termination') for row in rows):
                changed[0]=True;self.now=env['deadline']-1
                self.s.extend('alice',env['environment_id'],'extendkey',{'hours':1})
                self.now=env['deadline']
            return original(rows,expected)
        self.store.commit=interleaved
        self.assertIsNone(self.s.expire(env['environment_id']))
        self.assertEqual(self.store.get(env['pk'])['status'],'READY')
    @patch.dict(os.environ, {"USER_POOL_ID":"test-pool"})
    @patch("cloudhub.accounts.Accounts", autospec=True)
    def test_api_202_and_unified_error_and_auth(self, accounts):
        accounts.return_value.identity.return_value={"owner_id":"alice","email":"alice@example.com","email_verified":True}
        api.SERVICE=self.s;api.DISPATCH=None
        event={'rawPath':'/environments','headers':{'Idempotency-Key':'apirequest'},'body':json.dumps({'template_id':'T01','duration_hours':1}),
               'requestContext':{'requestId':'req','http':{'method':'POST'},'authorizer':{'jwt':{'claims':{'sub':'alice','token_use':'access'}}}}}
        result=api.handler(event);self.assertEqual(result['statusCode'],202);self.assertIn('operation_id',json.loads(result['body']))
        event['body']='[]';result=api.handler(event);self.assertEqual(result['statusCode'],400);self.assertIn('request_id',json.loads(result['body'])['error'])
        event['requestContext']['authorizer']['jwt']['claims']['token_use']='id';self.assertEqual(api.handler(event)['statusCode'],401)

if __name__=='__main__':unittest.main()
