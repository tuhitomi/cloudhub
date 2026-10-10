"""Optional shared network for review. No AWS calls; no NAT or public databases."""
import json
from pathlib import Path
def ref(x):return {'Ref':x}
def generate():
    tags=[{'Key':'cloudhub:shared','Value':'true'}]
    resources={
        'Vpc':{'Type':'AWS::EC2::VPC','Properties':{'CidrBlock':'10.42.0.0/16','EnableDnsSupport':True,'EnableDnsHostnames':True,'Tags':tags}},
        'Gateway':{'Type':'AWS::EC2::InternetGateway','Properties':{'Tags':tags}},
        'Attach':{'Type':'AWS::EC2::VPCGatewayAttachment','Properties':{'VpcId':ref('Vpc'),'InternetGatewayId':ref('Gateway')}},
        'PublicRoutes':{'Type':'AWS::EC2::RouteTable','Properties':{'VpcId':ref('Vpc'),'Tags':tags}},
        'DefaultInternet':{'Type':'AWS::EC2::Route','DependsOn':'Attach','Properties':{'RouteTableId':ref('PublicRoutes'),'DestinationCidrBlock':'0.0.0.0/0','GatewayId':ref('Gateway')}},
        'PrivateRoutes':{'Type':'AWS::EC2::RouteTable','Properties':{'VpcId':ref('Vpc'),'Tags':tags}}}
    for index,(name,cidr,public) in enumerate([('Public1','10.42.0.0/24',True),('Public2','10.42.1.0/24',True),('Db1','10.42.10.0/24',False),('Db2','10.42.11.0/24',False)]):
        resources[name]={'Type':'AWS::EC2::Subnet','Properties':{'VpcId':ref('Vpc'),'CidrBlock':cidr,'MapPublicIpOnLaunch':False,
            'AvailabilityZone':{'Fn::Select':[index%2,{'Fn::GetAZs':''}]},'Tags':tags}}
        resources[name+'Routes']={'Type':'AWS::EC2::SubnetRouteTableAssociation','Properties':{'SubnetId':ref(name),'RouteTableId':ref('PublicRoutes' if public else 'PrivateRoutes')}}
    return {'AWSTemplateFormatVersion':'2010-09-09','Description':'CloudHub optional shared 2-AZ network; review CIDR overlap first, no NAT.',
        'Resources':resources,'Outputs':{n:{'Value':ref(n)} for n in ['Vpc','Public1','Public2','Db1','Db2']}}
if __name__=='__main__':
    path=Path(__file__).resolve().parents[1]/'infra/network.json';path.write_text(json.dumps(generate(),indent=2),encoding='utf-8');print('Generated optional network; no resources created.')
