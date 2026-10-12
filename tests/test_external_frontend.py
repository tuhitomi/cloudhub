import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from generate_infra import build_template, frontend_urls
from build_pages_live import build, public_config
from configure_frontend import config_from_outputs


class ExternalFrontendTests(unittest.TestCase):
    def config(self):
        return {'configured': True, 'apiBase': 'https://api123.execute-api.ap-southeast-1.amazonaws.com',
                'clientId': 'client123', 'cognitoDomain': 'https://cloudhub-test.auth.ap-southeast-1.amazoncognito.com',
                'redirectUri': 'https://example.github.io/cloudhub/', 'region': 'ap-southeast-1'}

    def test_callback_path_and_origin_are_distinct(self):
        cf = build_template('https://example.github.io/cloudhub/')
        resources = cf['Resources']
        self.assertFalse(any(r['Type'].startswith('AWS::CloudFront::') for r in resources.values()))
        self.assertNotIn('FrontendBucket', resources)
        self.assertNotIn('FrontendDistribution', json.dumps(cf))
        client = resources['UserClient']['Properties']
        self.assertEqual(client['CallbackURLs'], ['https://example.github.io/cloudhub/'])
        self.assertEqual(client['LogoutURLs'], client['CallbackURLs'])
        self.assertFalse(client['GenerateSecret'])
        api = resources['HttpApi']['Properties']
        self.assertEqual(api['CorsConfiguration']['AllowOrigins'], ['https://example.github.io'])
        self.assertEqual(api['Auth']['DefaultAuthorizer'], 'CognitoJwt')
        for name in ['ApiFunction', 'WorkerFunction', 'SweepFunction']:
            driver = resources[name]['Properties']['Environment']['Variables']['DRIVER_SETTINGS']['Fn::Sub']
            self.assertEqual(json.loads(driver)['frontend_origin'], 'https://example.github.io')
        self.assertEqual(resources['Table']['DeletionPolicy'], 'Retain')
        self.assertEqual(cf['Parameters']['VerifiedTemplates']['Default'], '')

    def test_default_hosting_is_preserved(self):
        self.assertIn('FrontendDistribution', build_template()['Resources'])
        self.assertEqual(frontend_urls('https://example.github.io/cloudhub')[0], 'https://example.github.io/cloudhub/')

    def test_backup_permission_uses_supported_vault_attribute(self):
        template = build_template('https://example.github.io/cloudhub/')
        statements = template['Resources']['WorkerFunction']['Properties']['Policies'][0]['Statement']
        start = next(p for p in statements if p.get('Action') == 'backup:StartBackupJob')
        self.assertEqual(start['Resource'], {'Fn::GetAtt': ['BackupVault', 'BackupVaultArn']})

    def test_share_can_head_current_object_and_read_fixed_version(self):
        template = build_template('https://example.github.io/cloudhub/')
        statements = template['Resources']['ApiFunction']['Properties']['Policies'][0]['Statement']
        scoped = [p for p in statements if p.get('Resource') == {'Fn::Sub': 'arn:${AWS::Partition}:s3:::cloudhub-${AWS::AccountId}-${AWS::Region}-*/*'}]
        actions = {action for p in scoped for action in (p['Action'] if isinstance(p['Action'], list) else [p['Action']])}
        self.assertTrue({'s3:GetObject', 's3:GetObjectVersion'} <= actions)
        self.assertFalse({'s3:PutObject', 's3:DeleteObject'} & actions)

    def test_retained_data_has_no_duplicate_creation_or_dangling_attributes(self):
        names = {'Table': 'cloudhub-retained-table', 'BackupVault': 'cloudhub-retained-data',
                 'S3BackupBucket': 'cloudhub-retained-bucket'}
        template = build_template('https://example.github.io/cloudhub/', names)
        for name, value in names.items():
            self.assertNotIn(name, template['Resources'])
            self.assertEqual(template['Parameters'][name]['Default'], value)
        text = json.dumps(template)
        self.assertNotIn('${Table.Arn}', text)
        self.assertNotIn('${S3BackupBucket.Arn}', text)
        for name in names:
            self.assertNotIn('"Fn::GetAtt": ["' + name + '"', text)
        self.assertEqual(template['Resources']['S3BackupBucketPolicy']['Properties']['Bucket'], {'Ref': 'S3BackupBucket'})

    def test_invalid_callbacks_and_private_config_are_rejected(self):
        for url in ['http://example.com/', '/cloudhub/', 'https://name:secret@example.com/',
                    'https://example.com/?code=x', 'https://example.com/#token', 'https://example.com:8443/']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                frontend_urls(url)
        with self.assertRaises(ValueError):
            public_config({**self.config(), 'accessToken': 'private'})
        with self.assertRaises(ValueError):
            public_config({**self.config(), 'apiBase': 'https://api123.execute-api.us-east-1.amazonaws.com'})

    def test_live_artifact_is_text_only_and_refuses_mixed_output(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            self.assertEqual(build(target, self.config()), ['.nojekyll', 'app.js', 'auth.js', 'config.js', 'index.html', 'portal.js', 'style.css'])
            self.assertIn('href="./"', (target / 'index.html').read_text(encoding='utf-8'))
            self.assertIn("import {createPortal}", (target / 'app.js').read_text(encoding='utf-8'))
            for path in target.iterdir():
                self.assertNotIn(b'\x00', path.read_bytes())
            (target / 'backend.zip').write_text('must never publish')
            with self.assertRaises(ValueError):
                build(target, self.config())

    def test_failed_stack_cannot_enable_client(self):
        with self.assertRaises(ValueError):
            config_from_outputs({'Stacks': [{'StackStatus': 'ROLLBACK_COMPLETE', 'Outputs': []}]})
        config = self.config()
        keys = {'ApiUrl': 'apiBase', 'ClientId': 'clientId', 'CognitoDomain': 'cognitoDomain',
                'FrontendUrl': 'redirectUri', 'Region': 'region'}
        outputs = [{'OutputKey': key, 'OutputValue': config[field]} for key, field in keys.items()]
        self.assertEqual(config_from_outputs({'Stacks': [{'StackStatus': 'CREATE_COMPLETE', 'Outputs': outputs}]}), config)
