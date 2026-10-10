"""Trusted template helpers. No cloud credentials, template text or ARNs from a browser."""
def ref(name): return {"Ref":name}
def sub(value): return {"Fn::Sub":value}
def att(name,field='Arn'): return {"Fn::GetAtt":[name,field]}
def tags(env):
    return [{"Key":key,"Value":value} for key,value in {
        'cloudhub:environment':env['environment_id'],'cloudhub:owner':env['owner_id'],'cloudhub:managed':'true'}.items()]
def name(env,suffix=''):return 'cloudhub-'+env['environment_id']+suffix
def sg(env,settings,description,ingress=None):
    return {'Type':'AWS::EC2::SecurityGroup','Properties':{'VpcId':settings['vpc_id'],'GroupDescription':description,
        'SecurityGroupIngress':ingress or [],'SecurityGroupEgress':[{'IpProtocol':'-1','CidrIp':'0.0.0.0/0'}],'Tags':tags(env)}}
def role(env,settings,service,statements,suffix='app'):
    return {'Type':'AWS::IAM::Role','Properties':{'RoleName':name(env,'-'+suffix),'PermissionsBoundary':settings['permissions_boundary'],
        'AssumeRolePolicyDocument':{'Version':'2012-10-17','Statement':[{'Effect':'Allow','Principal':{'Service':service},'Action':'sts:AssumeRole'}]},
        'Policies':[{'PolicyName':'environment','PolicyDocument':{'Version':'2012-10-17','Statement':statements}}],'Tags':tags(env)}}
def allow(actions,resource,condition=None):
    value={'Effect':'Allow','Action':actions,'Resource':resource}
    if condition:value['Condition']=condition
    return value
def template(resources,outputs=None):
    return {'AWSTemplateFormatVersion':'2010-09-09','Resources':resources,'Outputs':{k:{'Value':v} for k,v in (outputs or {}).items()}}
def instance(env,settings,groups,profile,userdata):
    return {'Type':'AWS::EC2::Instance','Properties':{'ImageId':settings['ami_id'],'InstanceType':'t3.micro','IamInstanceProfile':profile,
        'MetadataOptions':{'HttpTokens':'required'},'CreditSpecification':{'CPUCredits':'standard'},'PropagateTagsToVolumeOnCreation':True,
        'NetworkInterfaces':[{'DeviceIndex':'0','AssociatePublicIpAddress':True,'SubnetId':settings['public_subnet_id'],'GroupSet':groups}],
        'BlockDeviceMappings':[{'DeviceName':'/dev/xvda','Ebs':{'VolumeSize':16,'VolumeType':'gp3','Encrypted':True,'DeleteOnTermination':True}}],
        'Tags':tags(env),'UserData':{'Fn::Base64':userdata}}}
