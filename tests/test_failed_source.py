"""A missing uncreated source may be reconciled; existing/unknown data stays protected."""
import sys,unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from botocore.exceptions import ClientError
from cloudhub.data_driver import DataDriver
from cloudhub.stack_driver import StackDriver
from cloudhub.cf_common import name
from cloudhub.service import Service,record
from cloudhub.store import MemoryStore
from cloudhub.errors import HubError

class FailedSourceTests(unittest.TestCase):
    def run_inventory(self,status='CREATE_FAILED',ready=False,error='ResourceNotFoundException',previous=False):
        env={'environment_id':'env-'+'a'*24,'owner_id':'alice','template_id':'T09'}
        if ready:env['ready_at']=1000
        table=name(env,'-data')
        if previous:env['missing_uncreated_source']={'type':'AWS::DynamoDB::Table','id':table,'error':'ResourceNotFoundException','failed_stack_status':'CREATE_FAILED'}
        def inventory(driver,target):
            target['resources']=[{'type':'AWS::DynamoDB::Table','id':table,'logical_id':'DataTable'}]
            target['source_ids']=[table];return target['resources']
        class Dynamo:
            def describe_table(self,TableName):
                if error:raise ClientError({'Error':{'Code':error,'Message':'test only'}},'DescribeTable')
                return {'Table':{'TableStatus':'ACTIVE'}}
        driver=DataDriver({}, {'dynamodb':Dynamo()});driver.assert_stack=lambda target:{'StackStatus':status}
        with patch.object(StackDriver,'inventory',inventory):
            try:driver.inventory(env)
            except ClientError:env['raised']=True
        return env,table
    def test_terminal_never_ready_table_not_found_is_recorded_without_claiming_backup(self):
        env,table=self.run_inventory()
        self.assertEqual(env['source_ids'],[])
        self.assertEqual(env['missing_uncreated_source']['id'],table)
        self.assertNotIn('backup',env)
    def test_existing_table_keeps_backup_guard(self):
        env,table=self.run_inventory(error=None)
        self.assertEqual(env['source_ids'],[table]);self.assertNotIn('missing_uncreated_source',env)
    def test_in_progress_creation_never_claims_source_absent(self):
        env,table=self.run_inventory(status='CREATE_IN_PROGRESS')
        self.assertEqual(env['source_ids'],[table]);self.assertNotIn('missing_uncreated_source',env)
    def test_once_ready_source_never_uses_failed_create_exception(self):
        env,table=self.run_inventory(ready=True)
        self.assertEqual(env['source_ids'],[table]);self.assertNotIn('missing_uncreated_source',env)
    def test_denied_or_unavailable_lookup_keeps_known_source_and_fails_closed(self):
        for error in ['AccessDeniedException','InternalServerError']:
            env,table=self.run_inventory(error=error)
            self.assertTrue(env['raised']);self.assertEqual(env['source_ids'],[table]);self.assertNotIn('missing_uncreated_source',env)
    def test_failed_missing_source_can_retire_after_read_only_audit_without_faking_backup(self):
        env,table=self.run_inventory();service=Service(MemoryStore(),clock=lambda:1000)
        row=record('environment',env['environment_id'],'alice',environment_id=env['environment_id'],template_id='T09',status='RESIDUAL',lock=None,source_ids=env['source_ids'],missing_uncreated_source=env['missing_uncreated_source'])
        service.save(row)
        op=service.command('alice',row['environment_id'],'destroy','retire_missing_source')
        self.assertEqual(op['phase'],'DELETE');self.assertIsNone(op['recovery_point'])
        self.assertEqual(service.store.scan_kind('recovery'),[])
    def test_residual_with_actual_or_unknown_source_cannot_use_missing_create_proof(self):
        for existing,ready,code in [(True,False,'ResourceNotFoundException'),(False,True,'ResourceNotFoundException'),(False,False,'AccessDeniedException')]:
            env,table=self.run_inventory();service=Service(MemoryStore(),clock=lambda:1000)
            proof={**env['missing_uncreated_source'],'error':code}
            row=record('environment',env['environment_id'],'alice',environment_id=env['environment_id'],template_id='T09',status='RESIDUAL',lock=None,source_ids=[table] if existing else [],missing_uncreated_source=proof)
            if ready:row['ready_at']=500
            service.save(row)
            with self.assertRaises(HubError):service.command('alice',row['environment_id'],'destroy','retire_protected_source')
            self.assertIsNone(service.store.get(row['pk'])['lock'])
    def test_each_delete_tick_rechecks_missing_source_instead_of_reviving_phantom_id(self):
        env,table=self.run_inventory(status='DELETE_IN_PROGRESS',previous=True)
        self.assertEqual(env['source_ids'],[])
        self.assertEqual(env['missing_uncreated_source']['failed_stack_status'],'CREATE_FAILED')
        without,table=self.run_inventory(status='DELETE_IN_PROGRESS')
        self.assertEqual(without['source_ids'],[table])
    def test_source_appearing_during_delete_remains_protected(self):
        env,table=self.run_inventory(status='DELETE_IN_PROGRESS',previous=True,error=None)
        self.assertEqual(env['source_ids'],[table])
    def test_read_only_audit_refreshes_inventory_before_reconciliation(self):
        env={};driver=DataDriver({},{});calls=[]
        def inventory(target):
            calls.append('inventory');target['source_ids']=[]
        def audit(instance,target,backups):
            calls.append('audit');self.assertEqual(target['source_ids'],[])
            return {'residual':[],'unknown':[]}
        driver.inventory=inventory
        with patch.object(StackDriver,'audit',audit):
            self.assertEqual(driver.audit(env,[]),{'residual':[],'unknown':[]})
        self.assertEqual(calls,['inventory','audit'])
