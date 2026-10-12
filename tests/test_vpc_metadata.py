"""ALB's required metadata read stays on the approved VPC and deployment role."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from generate_infra import build_template

class VPCMetadataTests(unittest.TestCase):
    def test_alb_metadata_read_cannot_inspect_other_vpcs_or_reach_student_roles(self):
        resources=build_template()['Resources']
        matches=[]
        for name,resource in resources.items():
            if name.startswith('EnvironmentPolicy'):
                for statement in resource['Properties']['PolicyDocument']['Statement']:
                    actions=statement['Action'] if isinstance(statement['Action'],list) else [statement['Action']]
                    if 'ec2:GetSecurityGroupsForVpc' in actions:matches.append((resource,statement))
        self.assertEqual(len(matches),1);resource,statement=matches[0]
        self.assertEqual(resource['Properties']['Roles'],[{'Ref':'EnvironmentCfRole'}])
        self.assertEqual(statement['Action'],'ec2:GetSecurityGroupsForVpc')
        self.assertEqual(statement['Resource'],{'Fn::Sub':'arn:${AWS::Partition}:ec2:${AWS::Region}:${AWS::AccountId}:vpc/${VpcId}'})
        self.assertEqual(statement['Condition'],{'StringEquals':{'aws:RequestedRegion':{'Ref':'AWS::Region'}}})
        self.assertNotIn('GetSecurityGroupsForVpc',str(resources['EnvironmentBoundary']))

if __name__=='__main__':unittest.main()
