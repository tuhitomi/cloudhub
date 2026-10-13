"""LOCAL simulations of lifecycle races, budgets and source preservation, never AWS proof."""
import sys,unittest
from decimal import Decimal
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from cloudhub.service import Service
from cloudhub.store import MemoryStore
from cloudhub.cost import Estimator
from cloudhub.errors import HubError
from cloudhub.worker import Worker
from cloudhub.operating_policy import plan,current_policy
from test_domain import RATES

class Processor:
    def __init__(self):self.calls=[];self.fail_backup=False;self.fail_schedule=False;self.data={'marker':'keep-this'}
    def create(self,e,o,r):self.calls.append('create');e.update(instance_id='i-owned',data_volume_id='vol-data',resources=[{'type':'AWS::EC2::Instance','id':'i-owned'},{'type':'AWS::EC2::Volume','id':'vol-data'}])
    def created(self,e):return True
    def ready(self,e,o,r):return True
    def schedule(self,e):
        self.calls.append('schedule')
        if self.fail_schedule:raise HubError('SCHEDULE_FAILED','SDK failed')
    def cancel_schedule(self,e):self.calls.append('cancel_schedule')
    def pause(self,e):self.calls.append('pause');return True
    def pause_audit(self,e,b):return {'deleted':[],'retained':e['resources'],'residual':[],'unknown':[],'billable':[{'type':'AWS::EC2::Volume','id':'vol-data'}]}
    def resume(self,e):self.calls.append('resume')
    def backup(self,e,o,r):
        self.calls.append('backup')
        if self.fail_backup:raise HubError('BACKUP_FAILED','no backup')
        return True
    def delete(self,e,o):self.calls.append('delete');return True
    def audit(self,e,b):return {'deleted':e['resources'],'retained':[],'residual':[],'unknown':[],'billable':[]}

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.now=1000;self.store=MemoryStore();self.service=Service(self.store,clock=lambda:self.now,estimator=Estimator(RATES),verified={'T01','T05'},budget_milliusd=100000,workflow_terminal=lambda op:True)
        self.driver=Processor();self.worker=Worker(self.service,self.driver)
    def policy(self,mode='AUTO_OFF',ending='PAUSE',budget=100000):
        return {'mode':mode,'period_minutes':5,'ending_policy':ending,'budget_limit_milliusd':budget if mode=='AUTO_RENEW' else None,'confirmed':True}
    def ready(self,policy=None,preview=8):
        policy=policy or self.policy();op=self.service.create('alice','create-lifecycle',{'template_id':'T05','estimate_hours':preview,'operating_policy':policy})
        for _ in range(10):
            if self.worker.tick(op['operation_id'])['done']:break
        return self.store.get('E#'+op['environment_id'])
    def finish(self,op):
        for _ in range(12):
            result=self.worker.tick(op['operation_id'],op.get('generation',0))
            if result['done']:return self.store.get('O#'+op['operation_id'])
        self.fail('local operation did not finish')
    def test_estimate_horizon_does_not_set_operating_deadline_and_auto_renew_is_explicit(self):
        env=self.ready(preview=8)
        self.assertEqual(env['deadline'],self.now+300);self.assertEqual(env['estimate_hours'],8)
        self.assertEqual(env['operating_policy']['mode'],'AUTO_OFF')
    def test_successful_automatic_renewal_preserves_ids_data_and_running_status(self):
        env=self.ready(self.policy('AUTO_RENEW'));self.now=env['deadline'];before=self.driver.data.copy();reserved=self.store.get('Q#GLOBAL')['reserved_milliusd']
        op=self.service.expire(env['environment_id'],env['deadline'],env['lifecycle_revision'])
        current=self.store.get(env['pk']);self.assertEqual(op['action'],'renew');self.assertEqual(current['status'],'READY')
        self.assertEqual(current['instance_id'],env['instance_id']);self.assertEqual(current['resources'],env['resources'])
        self.assertEqual(current['deadline'],env['deadline']+300);self.finish(op)
        self.assertEqual(self.driver.data,before);self.assertEqual(self.driver.calls.count('create'),1)
        self.assertNotIn('pause',self.driver.calls);self.assertNotIn('resume',self.driver.calls);self.assertNotIn('delete',self.driver.calls)
        self.assertGreater(self.store.get('Q#GLOBAL')['reserved_milliusd'],reserved)
    def test_old_schedule_is_ignored_even_when_the_new_deadline_is_now_due(self):
        env=self.ready(self.policy('AUTO_RENEW'));self.now=env['deadline'];op=self.service.expire(env['environment_id'],env['deadline'],env['lifecycle_revision']);self.finish(op)
        current=self.store.get(env['pk']);self.now=current['deadline']
        self.assertIsNone(self.service.expire(env['environment_id'],env['deadline'],env['lifecycle_revision']))
        self.assertEqual(self.store.get(env['pk'])['renewal_count'],1)
    def test_cumulative_cap_counts_initial_and_each_renewal_then_pauses(self):
        policy=self.policy('AUTO_RENEW');cost=plan(self.service.estimator,'T05',1,policy)
        policy['budget_limit_milliusd']=cost['initial_commitment_milliusd']+cost['renewal_period']['total_milliusd']
        env=self.ready(policy);self.now=env['deadline'];op=self.service.expire(env['environment_id']);self.finish(op)
        current=self.store.get(env['pk']);self.now=current['deadline'];ending=self.service.expire(env['environment_id']);self.finish(ending)
        ended=self.store.get(env['pk']);self.assertEqual(ended['status'],'PAUSED')
        self.assertEqual(ended['renewal_blocked_reason']['code'],'CUMULATIVE_BUDGET_EXCEEDED');self.assertFalse(ended['quota_released'])
        self.assertEqual(self.driver.calls.count('create'),1);self.assertNotIn('delete',self.driver.calls)
        self.assertEqual(self.service.notifications('alice')['items'][0]['type'],'RENEWAL_BLOCKED')
    def test_lowered_quota_blocks_renewal_and_applies_confirmed_ending_policy(self):
        env=self.ready(self.policy('AUTO_RENEW'));self.service.set_limits('admin','alice',{'max_active':0,'budget_milliusd':100000})
        self.now=env['deadline'];op=self.service.expire(env['environment_id']);self.assertEqual(op['action'],'pause');self.finish(op)
        self.assertEqual(self.store.get(env['pk'])['renewal_blocked_reason']['code'],'QUOTA_EXCEEDED')
    def test_quota_change_between_denial_and_commit_rechecks_before_ending(self):
        env=self.ready(self.policy('AUTO_RENEW'));self.service.set_limits('admin','alice',{'max_active':0,'budget_milliusd':100000});self.now=env['deadline']
        original=self.store.commit;interleaved=[False]
        def commit(rows,expected):
            if not interleaved[0] and any(r.get('action')=='pause' for r in rows):
                interleaved[0]=True;quota=self.store.get('Q#alice');quota['max_active']=1;original([quota],{quota['pk']:quota['version']})
            return original(rows,expected)
        self.store.commit=commit;op=self.service.expire(env['environment_id'])
        self.assertTrue(interleaved[0]);self.assertEqual(op['action'],'renew');self.assertNotIn('pause',self.driver.calls)
    def test_duplicate_manual_and_timer_retries_charge_only_once(self):
        env=self.ready();body={'minutes':5,'confirmed':True,'expected_revision':env['lifecycle_revision']}
        with ThreadPoolExecutor(max_workers=8) as pool:ops=list(pool.map(lambda _:self.service.renew('alice',env['environment_id'],'same-renewal-key',body),range(8)))
        self.assertEqual(len({o['operation_id'] for o in ops}),1);self.assertEqual(self.store.get(env['pk'])['renewal_count'],1)
        self.finish(ops[0]);again=self.service.renew('alice',env['environment_id'],'same-renewal-key',body);self.assertEqual(again['operation_id'],ops[0]['operation_id'])
    def test_manual_renewal_near_deadline_defeats_old_timer_and_policy_change_race(self):
        env=self.ready();self.now=env['deadline']-1
        op=self.service.renew('alice',env['environment_id'],'near-deadline-renew',{'minutes':5,'confirmed':True,'expected_revision':env['lifecycle_revision']})
        self.now=env['deadline'];self.assertIsNone(self.service.expire(env['environment_id'],env['deadline'],env['lifecycle_revision']))
        with self.assertRaises(HubError):self.service.change_policy('alice',env['environment_id'],'racing-mode-change',{'operating_policy':self.policy('AUTO_RENEW'),'expected_revision':env['lifecycle_revision']})
        self.finish(op);self.assertEqual(self.store.get(env['pk'])['status'],'READY')
    def test_scheduler_failure_retains_updated_deadline_and_retry_does_not_recharge(self):
        env=self.ready();op=self.service.renew('alice',env['environment_id'],'renew-sdk-failure',{'minutes':5,'confirmed':True})
        charged=self.store.get('Q#GLOBAL')['reserved_milliusd'];deadline=self.store.get(env['pk'])['deadline'];self.driver.fail_schedule=True;self.finish(op)
        failed=self.store.get('O#'+op['operation_id']);current=self.store.get(env['pk'])
        self.assertEqual(current['status'],'READY');self.assertTrue(current['schedule_pending']);self.assertEqual(current['deadline'],deadline)
        self.driver.fail_schedule=False;retry=self.service.retry_automatic_operation(failed);self.finish(retry)
        self.assertEqual(self.store.get('Q#GLOBAL')['reserved_milliusd'],charged);self.assertEqual(self.store.get(env['pk'])['deadline'],deadline)
        self.assertFalse(self.store.get(env['pk'])['schedule_pending']);self.assertEqual(self.driver.calls.count('create'),1)
    def test_mandatory_backup_failure_never_deletes_and_automatic_retries_are_bounded(self):
        env=self.ready(self.policy(ending='BACKUP_RETIRE'));self.now=env['deadline'];self.driver.fail_backup=True
        op=self.service.expire(env['environment_id']);self.finish(op)
        for count in [1,2]:
            op=self.service.retry_automatic_operation(self.store.get('O#'+op['operation_id']));self.assertEqual(op['automatic_retries'],count);self.finish(op)
        self.assertIsNone(self.service.retry_automatic_operation(self.store.get('O#'+op['operation_id'])))
        self.assertNotIn('delete',self.driver.calls);self.assertEqual(self.driver.calls.count('backup'),3)
        current=self.store.get(env['pk']);self.assertEqual(current['status'],'ERROR_RETAINED');self.assertTrue(current['needs_admin']);self.assertFalse(current['quota_released'])
    def test_pause_holds_volumes_and_quota_resume_preserves_resources_retire_releases_quota(self):
        env=self.ready();op=self.service.end('alice',env['environment_id'],'manual-end-pause',{'confirmed':True});self.finish(op)
        paused=self.store.get(env['pk']);self.assertEqual(paused['status'],'PAUSED');self.assertEqual(paused['pause_report']['deleted'],[])
        resumed=self.service.resume_paused('alice',env['environment_id'],'manual-resume-paused',{'confirmed':True});self.finish(resumed)
        current=self.store.get(env['pk']);self.assertEqual(current['status'],'READY');self.assertEqual(current['instance_id'],env['instance_id']);self.assertEqual(self.driver.data['marker'],'keep-this')
        retired=self.service.command('alice',env['environment_id'],'destroy','manual-force-retire',{});self.finish(retired)
        self.assertEqual(self.store.get(env['pk'])['status'],'DELETED');self.assertTrue(self.store.get(env['pk'])['quota_released'])
    def test_legacy_policy_never_enables_auto_renew_and_pause_is_template_scoped(self):
        self.assertEqual(current_policy({'duration_hours':2})['mode'],'AUTO_OFF')
        from cloudhub.operating_policy import validate_policy
        with self.assertRaises(HubError) as exc:validate_policy('T04',self.policy())
        self.assertEqual(exc.exception.code,'PAUSE_UNSUPPORTED')
    def test_unfenced_legacy_callback_cannot_act_after_revision_changes_even_if_current_deadline_due(self):
        from cloudhub import runtime
        from unittest.mock import patch
        env=self.ready();self.now=env['deadline']
        with patch.object(runtime,'build_service',return_value=self.service),patch.object(runtime,'dispatch') as dispatch:
            self.assertFalse(runtime.expiry_handler({'environment_id':env['environment_id']})['accepted'])
            dispatch.assert_not_called()
        self.assertEqual(self.store.get(env['pk'])['status'],'READY')
        self.assertIsNotNone(self.service.expire(env['environment_id'])) # Fresh fallback can still process the current deadline.
    def test_unmodified_legacy_environment_still_accepts_its_original_callback_without_enabling_renewal(self):
        from cloudhub import runtime
        from unittest.mock import patch
        env=self.ready()
        for key in ['lifecycle_revision','operating_policy','operating_schema']:env.pop(key,None)
        env['duration_hours']=1;self.service.save(env);self.now=env['deadline']
        with patch.object(runtime,'build_service',return_value=self.service),patch.object(runtime,'dispatch') as dispatch:
            self.assertTrue(runtime.expiry_handler({'environment_id':env['environment_id']})['accepted'])
            dispatch.assert_called_once()
        current=self.store.get(env['pk']);op=self.store.get('O#'+current['lock'])
        self.assertEqual(op['action'],'destroy');self.assertEqual(current_policy(current)['mode'],'AUTO_OFF')
    def test_policy_change_updates_future_prices_and_storage_allowance_without_resetting_ledger(self):
        env=self.ready();before=env['estimated_committed_milliusd'];policy=self.policy('AUTO_RENEW','BACKUP_RETIRE');policy['period_minutes']=15
        quote=self.service.management_estimate('alice',env['environment_id'],{'action':'change_policy','operating_policy':policy})
        self.assertEqual(quote['period']['assumptions']['duration_hours'],Decimal('0.25'))
        op=self.service.change_policy('alice',env['environment_id'],'change-price-policy',{'operating_policy':policy});self.finish(op)
        current=self.store.get(env['pk']);self.assertEqual(current['deadline'],env['deadline'])
        self.assertEqual(current['cost_plan']['after_shutdown']['ending_policy'],'BACKUP_RETIRE')
        self.assertEqual(current['estimated_committed_milliusd'],before+quote['additional_commitment_milliusd'])
    def test_management_quotes_accept_dynamo_decimal_policy_period_but_reject_fractional_input(self):
        env=self.ready();env['operating_policy']['period_minutes']=Decimal(5);self.service.save(env)
        for action in ['end','destroy','backup','resume_paused']:
            quote=self.service.management_estimate('alice',env['environment_id'],{'action':action})
            self.assertTrue(quote['approved']);self.assertEqual(quote['period']['assumptions']['duration_hours'],1 if action=='backup' else Decimal(5)/60)
        with self.assertRaises(HubError):self.service.management_estimate('alice',env['environment_id'],{'action':'renew','minutes':5.0})
    def test_cross_owner_cannot_manage_estimate_history_or_policy(self):
        env=self.ready();eid=env['environment_id']
        calls=[lambda:self.service.renew('bob',eid,'foreign-key-1',{'minutes':5,'confirmed':True}),
            lambda:self.service.change_policy('bob',eid,'foreign-key-2',{'operating_policy':self.policy('AUTO_RENEW')}),
            lambda:self.service.end('bob',eid,'foreign-key-3',{'confirmed':True}),
            lambda:self.service.resume_paused('bob',eid,'foreign-key-4',{'confirmed':True}),
            lambda:self.service.management_estimate('bob',eid,{'action':'renew'}),lambda:self.service.renewal_history('bob',eid)]
        for call in calls:
            with self.assertRaises(HubError) as exc:call()
            self.assertEqual(exc.exception.status,404)

if __name__=='__main__':unittest.main()
