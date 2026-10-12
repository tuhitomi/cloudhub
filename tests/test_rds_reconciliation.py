"""Confirmed missing RDS resources keep historical inventory and backup guards."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from botocore.exceptions import ClientError
from cloudhub.rds_driver import RDSDriver
from cloudhub.service import Service,record
from cloudhub.store import MemoryStore

class RDSReconciliationTests(unittest.TestCase):
    def discover(self,error,env):
        class RDS:
            def describe_db_instances(self,DBInstanceIdentifier):
                raise ClientError({'Error':{'Code':error,'Message':'test only'}},'DescribeDBInstances')
        RDSDriver({}, {'rds':RDS()}).discover(env)
    def test_confirmed_absence_keeps_saved_source_for_final_audit(self):
        for error in ['DBInstanceNotFound','DBInstanceNotFoundFault']:
            env={'environment_id':'env-'+'a'*24,'owner_id':'alice','resources':[{'type':'AWS::RDS::DBInstance','id':'own-db'}],'source_ids':['own-db']}
            self.discover(error,env)
            self.assertEqual(env['db_id'],'own-db')
            self.assertEqual(env['data_source'],'own-db')
            self.assertEqual(env['source_ids'],['own-db'])
            self.assertEqual(env['resources'][0]['id'],'own-db')
    def test_unknown_or_denied_lookup_never_claims_database_absent(self):
        for error in ['AccessDenied','Throttling','InternalFailure']:
            env={'resources':[{'type':'AWS::RDS::DBInstance','id':'own-db'}],'source_ids':['own-db']}
            with self.assertRaises(ClientError):self.discover(error,env)
            self.assertEqual(env['source_ids'],['own-db'])
    def test_discovery_does_not_allow_failed_rds_to_skip_final_backup(self):
        env=record('environment','env-'+'a'*24,'alice',environment_id='env-'+'a'*24,template_id='T03',status='CREATE_FAILED',lock=None,source_ids=['own-db'],resources=[{'type':'AWS::RDS::DBInstance','id':'own-db'}])
        self.discover('DBInstanceNotFound',env)
        service=Service(MemoryStore(),clock=lambda:1000);service.save(env)
        op=service.command('alice',env['environment_id'],'destroy','retire_missing_rds')
        self.assertEqual(op['phase'],'BACKUP')
        self.assertIsNotNone(op['recovery_point'])
