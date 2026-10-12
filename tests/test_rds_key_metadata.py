"""The deployment role can inspect default key metadata without crypto access."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from generate_infra import build_template

class RDSKeyMetadataTests(unittest.TestCase):
    def test_only_default_key_metadata_is_added_to_deployment_role(self):
        resources=build_template()['Resources'];statements=[]
        for name,r in resources.items():
            if name.startswith('EnvironmentPolicy'):statements.extend(r['Properties']['PolicyDocument']['Statement'])
        kms=[s for s in statements if any(a.startswith('kms:') for a in ([s['Action']] if isinstance(s['Action'],str) else s['Action']))]
        self.assertEqual(len(kms),1);stmt=kms[0]
        self.assertEqual(stmt['Action'],'kms:DescribeKey')
        self.assertEqual(stmt['Resource'],{'Fn::Sub':'arn:${AWS::Partition}:kms:${AWS::Region}:${AWS::AccountId}:key/*'})
        self.assertEqual(stmt['Condition']['ForAnyValue:StringEquals']['kms:ResourceAliases'],['alias/aws/rds','alias/aws/secretsmanager'])
        self.assertEqual(stmt['Condition']['StringEquals']['aws:RequestedRegion'],{'Ref':'AWS::Region'})
        boundary=resources['EnvironmentBoundary']['Properties']['PolicyDocument']['Statement']
        self.assertFalse(any('kms:' in str(s['Action']) for s in boundary))
