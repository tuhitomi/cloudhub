from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from generate_infra import build_template
from restore_aws_frontend import FRONTEND, restore_frontend


class FrontendMigrationTests(unittest.TestCase):
    def current(self):
        result = build_template('https://example.github.io/cloudhub/', {
            'Table': 'retained-table', 'BackupVault': 'retained-vault', 'S3BackupBucket': 'retained-bucket'})
        result['Parameters']['VerifiedTemplates']['Default'] = 'T07,T09'
        result['Resources']['ApiFunction']['Properties']['CodeUri'] = {'Bucket': 'artifact-bucket', 'Key': 'exact-existing.zip'}
        return result

    def test_preserves_backend_identity_artifacts_and_retained_data(self):
        current = self.current()
        before = deepcopy(current)
        result = restore_frontend(current)
        self.assertEqual(current, before)
        self.assertEqual(result['Parameters'], current['Parameters'])
        self.assertEqual(set(result['Resources']) - set(current['Resources']), set(FRONTEND))
        allowed = {'UserClient', 'HttpApi', 'ApiFunction', 'WorkerFunction', 'ExpiryFunction', 'SweepFunction'}
        for name, resource in current['Resources'].items():
            if name not in allowed:
                self.assertEqual(result['Resources'][name], resource, name)
        self.assertEqual(result['Resources']['ApiFunction']['Properties']['CodeUri'],
                         current['Resources']['ApiFunction']['Properties']['CodeUri'])
        for name in ('Table', 'BackupVault', 'S3BackupBucket'):
            self.assertNotIn(name, result['Resources'])

    def test_keeps_authenticated_backend_and_private_s3_origin(self):
        result = restore_frontend(self.current())
        r = result['Resources']
        self.assertEqual(r['HttpApi']['Properties']['Auth']['DefaultAuthorizer'], 'CognitoJwt')
        self.assertFalse(r['UserClient']['Properties']['GenerateSecret'])
        self.assertTrue(all(r['FrontendBucket']['Properties']['PublicAccessBlockConfiguration'].values()))
        self.assertEqual(r['OriginAccess']['Properties']['OriginAccessControlConfig']['SigningBehavior'], 'always')
        for name in ('ApiFunction', 'WorkerFunction', 'SweepFunction'):
            settings = r[name]['Properties']['Environment']['Variables']['DRIVER_SETTINGS']['Fn::Sub']
            self.assertEqual(json.loads(settings)['frontend_origin'], 'https://${FrontendDistribution.DomainName}')
        self.assertIn('aws.cognito.signin.user.admin', r['UserClient']['Properties']['AllowedOAuthScopes'])

    def test_refuses_partial_frontend_or_unknown_driver_expression(self):
        current = self.current()
        current['Resources']['FrontendBucket'] = {'Type': 'AWS::S3::Bucket'}
        with self.assertRaises(ValueError):
            restore_frontend(current)
        current = self.current()
        current['Resources']['ApiFunction']['Properties']['Environment']['Variables']['DRIVER_SETTINGS'] = 'unexpected'
        with self.assertRaises(ValueError):
            restore_frontend(current)

    def test_sdk_signin_keeps_issuer_audience_and_both_scoped_access_tokens(self):
        current = self.current()
        auth = current['Resources']['HttpApi']['Properties']['Auth']['Authorizers']['CognitoJwt']
        self.assertEqual(auth['AuthorizationScopes'], ['cloudhub/use', 'aws.cognito.signin.user.admin'])
        self.assertEqual(auth['JwtConfiguration']['audience'], [{'Ref': 'UserClient'}])
        self.assertIn('${UserPool}', auth['JwtConfiguration']['issuer']['Fn::Sub'])
        client = current['Resources']['UserClient']['Properties']
        self.assertFalse(client['GenerateSecret'])
        self.assertIn('ALLOW_USER_PASSWORD_AUTH', client['ExplicitAuthFlows'])
        self.assertEqual(client['AllowedOAuthFlows'], ['code'])
