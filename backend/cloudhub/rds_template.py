"""Shared T03 application/PostgreSQL resources, extended by T04 ALB/ASG."""
import base64
from pathlib import Path
from .cf_common import allow,att,instance,name,ref,role,sg,sub,tags,template

def user_data(settings):
    encoded=base64.b64encode(Path(__file__).with_name('rds_app.py').read_bytes()).decode()
    script="""#!/bin/bash
set -euo pipefail
dnf install -y python3.11 python3.11-pip
mkdir -p /opt/cloudhub
python3.11 -m venv /opt/cloudhub/venv
/opt/cloudhub/venv/bin/pip install boto3==1.43.110 'psycopg[binary]==3.2.13'
curl --fail --silent --show-error https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem -o /opt/cloudhub/rds-ca.pem
echo '${AppBase64}' | base64 --decode > /opt/cloudhub/app.py
cat > /opt/cloudhub/environment <<'ENV'
AWS_REGION=${AWS::Region}
DB_HOST=${Database.Endpoint.Address}
DB_SECRET=${Database.MasterUserSecret.SecretArn}
ENV
chmod 600 /opt/cloudhub/environment
cat > /etc/systemd/system/cloudhub-app.service <<'SERVICE'
[Unit]
After=network-online.target
Wants=network-online.target
[Service]
EnvironmentFile=/opt/cloudhub/environment
ExecStart=/opt/cloudhub/venv/bin/python /opt/cloudhub/app.py
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
SERVICE
systemctl daemon-reload
systemctl enable --now cloudhub-app
"""
    return {'Fn::Sub':[script,{'AppBase64':encoded}]}

def rds_template(env,settings,recovery=None):
    high=env['template_id']=='T04'
    ingress=[{'IpProtocol':'tcp','FromPort':8080,'ToPort':8080,'SourceSecurityGroupId':ref('LoadBalancerGroup')}] if high else [
        {'IpProtocol':'tcp','FromPort':8080,'ToPort':8080,'CidrIp':'0.0.0.0/0'}]
    r={'AppGroup':sg(env,settings,'CloudHub application',ingress),
       'DatabaseGroup':sg(env,settings,'Database receives only application traffic',[{'IpProtocol':'tcp','FromPort':5432,'ToPort':5432,'SourceSecurityGroupId':ref('AppGroup')}]),
       'DatabaseSubnets':{'Type':'AWS::RDS::DBSubnetGroup','Properties':{'DBSubnetGroupDescription':'CloudHub approved private DB subnets',
            'DBSubnetGroupName':name(env,'-db'),'SubnetIds':settings['db_subnet_ids'],'Tags':tags(env)}}}
    props={'DBInstanceIdentifier':name(env,'-db'),'DBInstanceClass':'db.t3.micro','Engine':'postgres',
        'DBSubnetGroupName':ref('DatabaseSubnets'),'VPCSecurityGroups':[ref('DatabaseGroup')],'PubliclyAccessible':False,
        'ManageMasterUserPassword':True,'MultiAZ':False,'BackupRetentionPeriod':0,'DeleteAutomatedBackups':True,
        'DeletionProtection':False,'CopyTagsToSnapshot':True,'StorageType':'gp3','Tags':tags(env),
        'EngineLifecycleSupport':'open-source-rds-extended-support-disabled'}
    if recovery:
        props['DBSnapshotIdentifier']=recovery['db_snapshot_id']
    else:
        props.update(AllocatedStorage='20',StorageEncrypted=True,DBName='cloudhub',MasterUsername='cloudhub',EngineVersion=settings['postgres_version'])
    r['Database']={'Type':'AWS::RDS::DBInstance','DeletionPolicy':'Delete','UpdateReplacePolicy':'Delete','Properties':props}
    r['AppRole']=role(env,settings,'ec2.amazonaws.com',[
        allow(['ssmmessages:CreateControlChannel','ssmmessages:CreateDataChannel','ssmmessages:OpenControlChannel','ssmmessages:OpenDataChannel','ssm:UpdateInstanceInformation'],'*'),
        allow('secretsmanager:GetSecretValue',att('Database','MasterUserSecret.SecretArn'))])
    r['AppProfile']={'Type':'AWS::IAM::InstanceProfile','Properties':{'InstanceProfileName':name(env,'-profile'),'Roles':[ref('AppRole')]}}
    outputs={'DatabaseId':ref('Database'),'SecretArn':att('Database','MasterUserSecret.SecretArn')}
    if high:
        r['LoadBalancerGroup']=sg(env,settings,'CloudHub ALB public read-only application',[{'IpProtocol':'tcp','FromPort':80,'ToPort':80,'CidrIp':'0.0.0.0/0'}])
        r['LoadBalancer']={'Type':'AWS::ElasticLoadBalancingV2::LoadBalancer','Properties':{'Name':('ch-'+env['environment_id'][4:])[:32],
            'Subnets':settings['public_subnet_ids'],'SecurityGroups':[ref('LoadBalancerGroup')],'Scheme':'internet-facing','Type':'application','Tags':tags(env)}}
        r['TargetGroup']={'Type':'AWS::ElasticLoadBalancingV2::TargetGroup','Properties':{'Name':('ch-'+env['environment_id'][4:]+'-tg')[:32],'VpcId':settings['vpc_id'],'Port':8080,'Protocol':'HTTP',
            'HealthCheckPath':'/health','TargetType':'instance','TargetGroupAttributes':[{'Key':'deregistration_delay.timeout_seconds','Value':'30'}],'Tags':tags(env)}}
        r['Listener']={'Type':'AWS::ElasticLoadBalancingV2::Listener','Properties':{'LoadBalancerArn':ref('LoadBalancer'),'Port':80,'Protocol':'HTTP',
            'DefaultActions':[{'Type':'forward','TargetGroupArn':ref('TargetGroup')}]}}
        r['LaunchTemplate']={'Type':'AWS::EC2::LaunchTemplate','Properties':{'LaunchTemplateName':name(env,'-launch'),'TagSpecifications':[{'ResourceType':'launch-template','Tags':tags(env)}],
            'LaunchTemplateData':{'ImageId':settings['ami_id'],'InstanceType':'t3.micro','IamInstanceProfile':{'Name':ref('AppProfile')},
                'CreditSpecification':{'CpuCredits':'standard'},'MetadataOptions':{'HttpTokens':'required'},
                'NetworkInterfaces':[{'DeviceIndex':0,'AssociatePublicIpAddress':True,'Groups':[ref('AppGroup')]}],
                'BlockDeviceMappings':[{'DeviceName':'/dev/xvda','Ebs':{'VolumeSize':16,'VolumeType':'gp3','Encrypted':True,'DeleteOnTermination':True}}],
                'TagSpecifications':[{'ResourceType':kind,'Tags':tags(env)} for kind in ['instance','volume','network-interface']],
                'UserData':{'Fn::Base64':user_data(settings)}}}}
        r['ScalingGroup']={'Type':'AWS::AutoScaling::AutoScalingGroup','DependsOn':'Listener','Properties':{'AutoScalingGroupName':name(env,'-asg'),
            'MinSize':'1','MaxSize':'2','DesiredCapacity':'1','VPCZoneIdentifier':settings['public_subnet_ids'],
            'HealthCheckType':'ELB','HealthCheckGracePeriod':600,'TargetGroupARNs':[ref('TargetGroup')],
            'LaunchTemplate':{'LaunchTemplateId':ref('LaunchTemplate'),'Version':att('LaunchTemplate','LatestVersionNumber')},
            'Tags':[{**tag,'PropagateAtLaunch':True} for tag in tags(env)]}}
        r['ScalingPolicy']={'Type':'AWS::AutoScaling::ScalingPolicy','Properties':{'AutoScalingGroupName':ref('ScalingGroup'),'PolicyType':'TargetTrackingScaling',
            'TargetTrackingConfiguration':{'PredefinedMetricSpecification':{'PredefinedMetricType':'ASGAverageCPUUtilization'},'TargetValue':60}}}
        outputs.update(ScalingGroup=ref('ScalingGroup'),LoadBalancerArn=ref('LoadBalancer'),TargetGroupArn=ref('TargetGroup'),WebsiteUrl=sub('http://${LoadBalancer.DNSName}'))
    else:
        r['Instance']=instance(env,settings,[ref('AppGroup')],ref('AppProfile'),user_data(settings))
        outputs.update(InstanceId=ref('Instance'),WebsiteUrl=sub('http://${Instance.PublicIp}:8080'))
    return template(r,outputs)
