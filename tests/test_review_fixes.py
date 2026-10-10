"""Regression checks for R01–R06. Local mocks/SDK Stubber, never live AWS evidence."""
import contextlib
import importlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from cloudhub import api, runtime
from cloudhub.aws_driver import AWSDriver
from cloudhub.cost import Estimator
from cloudhub.errors import Conflict, HubError
from cloudhub.service import Service
from cloudhub.store import MemoryStore
from cloudhub.worker import Worker
from test_domain import Driver, RATES


class RetirementSafetyTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.store, self.driver = MemoryStore(), Driver()
        self.service = Service(self.store, clock=lambda: self.now, estimator=Estimator(RATES),
                               verified={'T01'}, budget_milliusd=100000)
        self.worker = Worker(self.service, self.driver)
        create = self.service.create('alice', 'createkey', {'template_id': 'T01', 'duration_hours': 1})
        self.run_op(create)
        self.env = self.store.get('E#' + create['environment_id'])

    def run_op(self, op):
        for _ in range(12):
            result = self.worker.tick(op['operation_id'])
            if result['done']:
                return result
        self.fail('Operation did not terminate')

    def retiring(self):
        op = self.service.command('alice', self.env['environment_id'], 'destroy', 'retirekey')
        self.worker.tick(op['operation_id'])
        return self.store.get(op['pk']), self.store.get('R#' + op['recovery_point'])

    def test_final_backup_cannot_be_deleted_until_source_audit_is_clean(self):
        op, rp = self.retiring()
        self.assertEqual(rp['status'], 'AVAILABLE')
        self.assertTrue(api.public(rp)['retirement_protected'])
        self.assertNotIn('retirement_lock', api.public(rp))
        with self.assertRaises(HubError) as error:
            self.service.backup_command('alice', rp['recovery_point'], 'delete_backup', 'deletekey', {})
        self.assertEqual(error.exception.code, 'RECOVERY_LOCKED')
        self.assertNotIn('delete', self.driver.calls)
        self.assertFalse(self.run_op(op)['failed'])
        self.assertIsNone(self.store.get(rp['pk'])['retirement_lock'])
        deletion = self.service.backup_command('alice', rp['recovery_point'], 'delete_backup', 'deletekey', {})
        self.driver.expire_backup = Mock(return_value=True)
        self.worker.tick(deletion['operation_id'])
        self.assertEqual(self.store.get(rp['pk'])['status'], 'DELETED')

    def test_failure_and_cancellation_keep_final_backup_protected(self):
        op, rp = self.retiring()
        with contextlib.redirect_stdout(io.StringIO()):
            self.worker.fail(op['operation_id'], 'provider refused DELETE')
        self.service.control_operation('alice', op['operation_id'], 'cancel', 'cancelkey', {})
        with self.assertRaises(HubError):
            self.service.backup_command('alice', rp['recovery_point'], 'delete_backup', 'deletekey', {})
        self.assertEqual(self.store.get(rp['pk'])['retirement_lock'], op['operation_id'])
        self.assertNotIn('delete', self.driver.calls)

    def test_delete_retry_refuses_missing_deleted_or_expired_final_backup(self):
        op, rp = self.retiring()
        with contextlib.redirect_stdout(io.StringIO()):
            self.worker.fail(op['operation_id'], 'provider refused DELETE')
        for status in ['DELETED', 'EXPIRED']:
            row = self.store.get(rp['pk'])
            row['status'] = status
            self.service.save(row)
            with self.subTest(status=status), self.assertRaises(HubError) as error:
                self.service.control_operation('alice', op['operation_id'], 'retry', 'retrykey', {})
            self.assertEqual(error.exception.code, 'FINAL_BACKUP_UNAVAILABLE')
        self.store.rows.pop(rp['pk'])
        with self.assertRaises(HubError):
            self.service.control_operation('alice', op['operation_id'], 'retry', 'retrykey', {})
        self.assertNotIn('delete', self.driver.calls)

    def test_worker_checks_backup_again_before_irreversible_delete(self):
        op, rp = self.retiring()
        rp['status'] = 'DELETED'
        self.service.save(rp)
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.worker.tick(op['operation_id'])
        self.assertTrue(result['failed'])
        self.assertNotIn('delete', self.driver.calls)
        self.assertEqual(self.store.get(self.env['pk'])['status'], 'ERROR_RETAINED')

    def test_legacy_backup_acquisition_conflict_never_calls_source_delete(self):
        op, rp = self.retiring()
        rp.pop('retirement_lock')
        self.service.save(rp)
        with patch.object(self.store, 'commit', side_effect=Conflict()):
            with self.assertRaises(Conflict):
                self.worker.tick(op['operation_id'])
        self.assertNotIn('delete', self.driver.calls)

    def test_retention_sweep_skips_failed_retirement_backup(self):
        op, rp = self.retiring()
        with contextlib.redirect_stdout(io.StringIO()):
            self.worker.fail(op['operation_id'], 'provider refused DELETE')
        self.now = rp['retain_until'] + 1
        self.driver.expire_backup = Mock(return_value=True)
        with patch.object(runtime, 'build_service', return_value=self.service), patch.object(runtime, 'build_driver', return_value=self.driver):
            runtime.sweep_handler({})
        self.driver.expire_backup.assert_not_called()
        self.assertEqual(self.store.get(rp['pk'])['status'], 'AVAILABLE')

    def test_expired_point_is_preserved_but_source_delete_is_refused(self):
        op, rp = self.retiring()
        self.now = rp['retain_until'] + 1
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.worker.tick(op['operation_id'])
        self.assertTrue(result['failed'])
        self.assertNotIn('delete', self.driver.calls)

    def test_residual_and_unknown_cleanup_emit_alert_and_keep_quota_and_backup(self):
        for field in ['residual', 'unknown']:
            with self.subTest(field=field):
                op, rp = self.retiring()
                self.driver.audit = Mock(return_value={'deleted': [], 'retained': [], 'residual': [],
                                                      'unknown': [], 'billable': []})
                self.driver.audit.return_value[field] = [{'id': 'resource-review'}]
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    result = self.run_op(op)
                self.assertTrue(result['failed'])
                self.assertEqual(self.store.get(op['pk'])['status'], 'FAILED')
                self.assertEqual(self.store.get(self.env['pk'])['status'], 'RESIDUAL')
                self.assertEqual(self.store.get('Q#alice')['active'], 1)
                self.assertEqual(self.store.get(rp['pk'])['retirement_lock'], op['operation_id'])
                event = json.loads(output.getvalue())
                self.assertEqual(event['event'], 'CloudHubCleanupResidual')
                self.assertEqual(event[field + '_count'], 1)
                self.driver.audit.return_value[field] = []
                retry = self.service.control_operation('alice', op['operation_id'], 'retry', 'retrykey', {})
                self.assertFalse(self.run_op(retry)['failed'])
                self.assertEqual(self.store.get('Q#alice')['active'], 0)
                self.assertIsNone(self.store.get(rp['pk'])['retirement_lock'])
                if field == 'residual':
                    self.setUp()


class ValidationAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.service = Service(MemoryStore(), clock=lambda: 1000, estimator=Estimator(RATES),
                               verified={'T01'}, validation={'T05'}, budget_milliusd=100000)
        self.original = api.SERVICE, api.DISPATCH
        api.SERVICE, api.DISPATCH = self.service, None
        self.addCleanup(lambda: setattr(api, 'SERVICE', self.original[0]))
        self.addCleanup(lambda: setattr(api, 'DISPATCH', self.original[1]))
        self.environment = patch.dict(os.environ, {'USER_POOL_ID': 'test-pool'})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def event(self, validation=True, admin=True):
        return {'rawPath': '/environments', 'headers': {'Idempotency-Key': 'validationkey'},
                'requestContext': {'requestId': 'review', 'http': {'method': 'POST'},
                                  'authorizer': {'jwt': {'claims': {'sub': 'alice', 'username': 'alice',
                                     'token_use': 'access', 'cognito:groups': ['admin'] if admin else []}}}},
                'body': json.dumps({'template_id': 'T05' if validation else 'T01',
                                    'duration_hours': 1, 'validation_run': validation})}

    def test_removed_admin_jwt_cannot_create_validation_environment(self):
        with patch('cloudhub.accounts.Accounts', autospec=True) as accounts:
            accounts.return_value.assert_admin.side_effect = HubError('FORBIDDEN', 'removed', 403)
            self.assertEqual(api.handler(self.event())['statusCode'], 403)
            accounts.return_value.assert_admin.assert_called_once_with('alice', 'alice')
        self.assertIsNone(self.service.store.get('Q#alice'))

    def test_current_admin_can_create_validation_environment(self):
        with patch('cloudhub.accounts.Accounts', autospec=True) as accounts:
            self.assertEqual(api.handler(self.event())['statusCode'], 202)
            accounts.return_value.assert_admin.assert_called_once_with('alice', 'alice')

    def test_normal_create_does_not_require_admin_lookup(self):
        with patch('cloudhub.accounts.Accounts', autospec=True) as accounts:
            self.assertEqual(api.handler(self.event(validation=False, admin=False))['statusCode'], 202)
            accounts.assert_not_called()

    def test_non_admin_validation_is_denied_before_provider_lookup(self):
        with patch('cloudhub.accounts.Accounts', autospec=True) as accounts:
            self.assertEqual(api.handler(self.event(admin=False))['statusCode'], 403)
            accounts.assert_not_called()


class DataApiAccountTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('cloudhub.data_api_app')
        self.ddb = Mock()
        patcher = patch.object(self.module, 'ddb', self.ddb)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.dict(os.environ, {'OWNER': 'alice', 'ENVIRONMENT': 'env-review',
                                         'MANAGEMENT': 'meta', 'DATA_TABLE': 'data'})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.event = {'requestContext': {'authorizer': {'jwt': {'claims': {'sub': 'alice', 'token_use': 'access'}}}}}

    def test_disabled_owner_receives_no_data_with_unexpired_jwt(self):
        self.ddb.get_item.return_value = {'Item': {'enabled': {'BOOL': False}}}
        self.assertEqual(self.module.handler(self.event)['statusCode'], 403)
        self.ddb.get_item.assert_called_once_with(TableName='meta', Key={'pk': {'S': 'U#alice'}}, ConsistentRead=True)
        self.ddb.scan.assert_not_called()

    def test_enabled_and_existing_owner_without_account_record_can_read(self):
        for account in [{'Item': {'enabled': {'BOOL': True}}}, {}]:
            with self.subTest(account=account):
                self.ddb.get_item.side_effect = [account, {'Item': {'status': {'S': 'READY'}, 'deadline': {'N': '9999999999'}}}]
                self.ddb.scan.return_value = {'Items': [{'row_id': {'S': 'R#example'}, 'text': {'S': 'sample'}}]}
                response = self.module.handler(self.event)
                self.assertEqual(response['statusCode'], 200)
                self.assertEqual(json.loads(response['body'])['items'], [{'record_id': 'example', 'text': 'sample'}])

    def test_other_owner_cannot_probe_account_or_environment(self):
        self.event['requestContext']['authorizer']['jwt']['claims']['sub'] = 'bob'
        self.assertEqual(self.module.handler(self.event)['statusCode'], 404)
        self.ddb.get_item.assert_not_called()


class EbsExpiryTests(unittest.TestCase):
    def setUp(self):
        from botocore.exceptions import ClientError
        self.client_error = ClientError
        self.driver = AWSDriver.__new__(AWSDriver)
        self.driver.settings = {'region': 'us-east-1', 'account_id': '123456789012', 'backup_vault': 'test-vault'}
        self.driver.aws_backup, self.driver.ec2 = Mock(), Mock()
        self.rp = {'recovery_point': 'rp-test', 'owner_id': 'alice', 'environment_id': 'env-test',
                   'source': 'vol-0123456789abcdef0', 'backup_job_id': 'job-test'}
        self.arn = 'arn:aws:ec2:us-east-1:123456789012:snapshot/snap-0123456789abcdef0'
        self.tags = {'cloudhub:recovery': 'rp-test', 'cloudhub:owner': 'alice', 'cloudhub:environment': 'env-test'}
        self.volume_arn = 'arn:aws:ec2:us-east-1:123456789012:volume/' + self.rp['source']
        self.driver.aws_backup.describe_backup_job.return_value = {'State': 'COMPLETED', 'RecoveryPointArn': self.arn, 'ResourceArn': self.volume_arn}
        self.driver.aws_backup.describe_recovery_point.return_value = {'Status': 'COMPLETED', 'ResourceArn': self.volume_arn}
        self.driver.aws_backup.list_tags.return_value = {'Tags': self.tags}
        self.driver.ec2.describe_snapshots.return_value = {'Snapshots': [{'Tags': [{'Key': k, 'Value': v} for k, v in self.tags.items()]}]}

    def not_found(self, code):
        return self.client_error({'Error': {'Code': code, 'Message': 'not found'}}, 'Review')

    def test_completed_job_without_snapshot_metadata_is_reconciled_and_deleted(self):
        self.assertFalse(self.driver.expire_backup(self.rp))
        self.driver.aws_backup.describe_backup_job.assert_called_once_with(BackupJobId='job-test')
        self.driver.aws_backup.delete_recovery_point.assert_called_once_with(BackupVaultName='test-vault', RecoveryPointArn=self.arn)
        self.assertEqual(self.rp['cleanup_arns'], [self.arn])

    def test_pending_job_does_not_report_deleted(self):
        self.driver.aws_backup.describe_backup_job.return_value = {'State': 'RUNNING', 'ResourceArn': self.volume_arn}
        self.assertFalse(self.driver.expire_backup(self.rp))
        self.driver.aws_backup.delete_recovery_point.assert_not_called()

    def test_missing_job_id_discovers_owned_point_after_lost_response(self):
        self.rp.pop('backup_job_id')
        foreign = self.arn.replace('snap-0123456789abcdef0', 'snap-abcdef01234567890')
        self.driver.aws_backup.list_recovery_points_by_backup_vault.side_effect = [
            {'RecoveryPoints': [{'RecoveryPointArn': foreign}], 'NextToken': 'next'},
            {'RecoveryPoints': [{'RecoveryPointArn': self.arn}]}]
        self.driver.aws_backup.list_tags.side_effect = [{'Tags': dict(self.tags, **{'cloudhub:owner': 'bob'})},
                                                       {'Tags': self.tags}, {'Tags': self.tags}]
        self.assertFalse(self.driver.expire_backup(self.rp))
        self.driver.aws_backup.delete_recovery_point.assert_called_once_with(BackupVaultName='test-vault', RecoveryPointArn=self.arn)
        self.assertEqual(self.driver.aws_backup.list_recovery_points_by_backup_vault.call_args.kwargs['NextToken'], 'next')

    def test_empty_discovery_cannot_prove_no_backup(self):
        self.rp.pop('backup_job_id')
        self.driver.aws_backup.list_recovery_points_by_backup_vault.return_value = {'RecoveryPoints': []}
        self.driver.ec2.describe_snapshots.return_value = {'Snapshots': []}
        with self.assertRaises(HubError) as error:
            self.driver.expire_backup(self.rp)
        self.assertEqual(error.exception.code, 'RECOVERY_UNCONFIRMED')

    def test_terminal_failed_job_checks_vault_and_owned_snapshots_before_success(self):
        self.driver.aws_backup.describe_backup_job.return_value = {'State': 'FAILED', 'ResourceArn': self.volume_arn}
        self.driver.aws_backup.list_recovery_points_by_backup_vault.return_value = {'RecoveryPoints': []}
        self.driver.ec2.describe_snapshots.return_value = {'Snapshots': []}
        self.assertTrue(self.driver.expire_backup(self.rp))
        self.driver.aws_backup.list_recovery_points_by_backup_vault.assert_called_once_with(BackupVaultName='test-vault')
        filters = self.driver.ec2.describe_snapshots.call_args.kwargs
        self.assertEqual(filters['OwnerIds'], ['123456789012'])
        self.assertEqual({f['Name']: f['Values'] for f in filters['Filters']}, {
            'tag:cloudhub:recovery': ['rp-test'], 'tag:cloudhub:owner': ['alice'], 'tag:cloudhub:environment': ['env-test']})

    def test_failed_job_with_orphan_snapshot_is_not_reported_deleted(self):
        self.driver.aws_backup.describe_backup_job.return_value = {'State': 'FAILED', 'ResourceArn': self.volume_arn}
        self.driver.aws_backup.list_recovery_points_by_backup_vault.return_value = {'RecoveryPoints': []}
        self.driver.ec2.describe_snapshots.return_value['Snapshots'][0]['SnapshotId'] = self.arn.rsplit('/', 1)[1]
        self.driver.aws_backup.describe_recovery_point.side_effect = self.not_found('ResourceNotFoundException')
        with self.assertRaises(HubError) as error:
            self.driver.expire_backup(self.rp)
        self.assertEqual(error.exception.code, 'RECOVERY_UNCONFIRMED')

    def test_only_vault_and_snapshot_absence_prove_deletion(self):
        self.driver.aws_backup.describe_recovery_point.side_effect = self.not_found('ResourceNotFoundException')
        self.driver.ec2.describe_snapshots.side_effect = self.not_found('InvalidSnapshot.NotFound')
        self.assertTrue(self.driver.expire_backup(self.rp))
        self.driver.aws_backup.delete_recovery_point.assert_not_called()

    def test_snapshot_absence_alone_does_not_report_deleted(self):
        self.driver.ec2.describe_snapshots.side_effect = self.not_found('InvalidSnapshot.NotFound')
        self.assertFalse(self.driver.expire_backup(self.rp))
        self.driver.aws_backup.delete_recovery_point.assert_called_once()

    def test_orphan_snapshot_is_not_marked_deleted(self):
        self.driver.aws_backup.describe_recovery_point.side_effect = self.not_found('ResourceNotFoundException')
        with self.assertRaises(HubError) as error:
            self.driver.expire_backup(self.rp)
        self.assertEqual(error.exception.code, 'RECOVERY_UNCONFIRMED')

    def test_wrong_point_owner_cannot_be_deleted(self):
        self.driver.aws_backup.list_tags.return_value = {'Tags': dict(self.tags, **{'cloudhub:owner': 'bob'})}
        with self.assertRaises(HubError):
            self.driver.expire_backup(self.rp)
        self.driver.aws_backup.delete_recovery_point.assert_not_called()

    def test_wrong_job_source_cannot_be_deleted(self):
        self.driver.aws_backup.describe_backup_job.return_value['ResourceArn'] = self.volume_arn + 'foreign'
        with self.assertRaises(HubError):
            self.driver.expire_backup(self.rp)
        self.driver.aws_backup.delete_recovery_point.assert_not_called()

    def test_access_denied_never_becomes_deleted(self):
        self.driver.ec2.describe_snapshots.side_effect = self.not_found('AccessDenied')
        with self.assertRaises(self.client_error):
            self.driver.expire_backup(self.rp)

    def test_native_reconciliation_requests_match_real_sdk_shapes(self):
        import boto3
        from botocore.stub import Stubber
        backup = boto3.client('backup', region_name='us-east-1', aws_access_key_id='local', aws_secret_access_key='local')
        ec2 = boto3.client('ec2', region_name='us-east-1', aws_access_key_id='local', aws_secret_access_key='local')
        b, e = Stubber(backup), Stubber(ec2)
        b.add_response('describe_backup_job', {'State': 'COMPLETED', 'RecoveryPointArn': self.arn, 'ResourceArn': self.volume_arn}, {'BackupJobId': 'job-test'})
        b.add_response('describe_recovery_point', {'Status': 'COMPLETED', 'ResourceArn': self.volume_arn}, {'BackupVaultName': 'test-vault', 'RecoveryPointArn': self.arn})
        b.add_response('list_tags', {'Tags': self.tags}, {'ResourceArn': self.arn})
        b.add_response('delete_recovery_point', {}, {'BackupVaultName': 'test-vault', 'RecoveryPointArn': self.arn})
        e.add_response('describe_snapshots', self.driver.ec2.describe_snapshots.return_value, {'SnapshotIds': [self.arn.rsplit('/', 1)[1]]})
        self.driver.aws_backup, self.driver.ec2 = backup, ec2
        with b, e:
            self.assertFalse(self.driver.expire_backup(self.rp))
        b.assert_no_pending_responses()
        e.assert_no_pending_responses()


class SecurityInfrastructureTests(unittest.TestCase):
    def setUp(self):
        self.resources = json.loads((ROOT / 'infra/control-plane.json').read_text())['Resources']

    def test_student_instance_role_cannot_read_parameters_or_keypairs(self):
        role = self.resources['SessionRole']['Properties']
        self.assertFalse(role.get('ManagedPolicyArns'))
        statements = role['Policies'][0]['PolicyDocument']['Statement']
        denied = {a for p in statements if p['Effect'] == 'Deny' for a in p['Action']}
        self.assertTrue({'ssm:GetParameter', 'ssm:GetParameters', 'ssm:GetParametersByPath'} <= denied)
        allowed = {a for p in statements if p['Effect'] == 'Allow' for a in p['Action']}
        self.assertEqual(allowed, {'ssm:UpdateInstanceInformation', 'ssmmessages:CreateControlChannel',
                                  'ssmmessages:CreateDataChannel', 'ssmmessages:OpenControlChannel', 'ssmmessages:OpenDataChannel'})

    def test_residual_metric_routes_to_sns_alarm(self):
        metric = self.resources['ResidualMetric']['Properties']
        alarm = self.resources['ResidualAlarm']['Properties']
        self.assertIn('CloudHubCleanupResidual', metric['FilterPattern'])
        self.assertEqual(metric['MetricTransformations'][0]['MetricName'], alarm['MetricName'])
        self.assertEqual(alarm['AlarmActions'], [{'Ref': 'AlertTopic'}])

    def test_data_account_read_is_bounded_to_owner_in_policy_and_boundary(self):
        from cloudhub.data_template import data_template
        env = {'environment_id': 'env-review', 'owner_id': 'alice', 'template_id': 'T09'}
        settings = {'permissions_boundary': 'arn:aws:iam::123456789012:policy/boundary',
                    'management_table_arn': 'arn:aws:dynamodb:us-east-1:123456789012:table/meta',
                    'management_table': 'meta', 'frontend_origin': 'https://example.com',
                    'region': 'us-east-1', 'cognito_client_id': 'client', 'cognito_issuer': 'https://example.com'}
        statements = data_template(env, settings)['Resources']['ReadRole']['Properties']['Policies'][0]['PolicyDocument']['Statement']
        get_item = next(p for p in statements if 'dynamodb:GetItem' in p['Action'])
        self.assertEqual(get_item['Condition']['ForAllValues:StringEquals']['dynamodb:LeadingKeys'], ['E#env-review', 'U#alice'])
        boundary = self.resources['EnvironmentBoundary']['Properties']['PolicyDocument']['Statement']
        condition = next(p for p in boundary if 'dynamodb:GetItem' in p['Action'])['Condition']
        self.assertEqual(condition['ForAllValues:StringLike']['dynamodb:LeadingKeys'], [
            {'Fn::Sub': 'E#${!aws:PrincipalTag/cloudhub:environment}'}, {'Fn::Sub': 'U#${!aws:PrincipalTag/cloudhub:owner}'}])
