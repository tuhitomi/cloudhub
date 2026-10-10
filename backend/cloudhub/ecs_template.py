"""T06 fixed image digest, one Fargate task and registered S3 dataset."""
import re
from .cf_common import allow,att,name,ref,role,sg,sub,tags,template
from .s3_template import s3_template
from .errors import HubError
def ecs_template(env,settings,recovery=None):
    image=settings['approved_image']
    pattern=re.escape(settings['account_id'])+r'\.dkr\.ecr\.'+re.escape(settings['region'])+r'\.amazonaws\.com/[a-z0-9._/-]+@sha256:[a-f0-9]{64}'
    if not re.fullmatch(pattern,image):raise HubError('IMAGE_NOT_APPROVED','Cần image ECR cùng account/region, khóa bằng digest đã duyệt.',409)
    r=s3_template(env,settings)['Resources']
    r['LoadBalancerGroup']=sg(env,settings,'CloudHub Docker public read demo',[{'IpProtocol':'tcp','FromPort':80,'ToPort':80,'CidrIp':'0.0.0.0/0'}])
    r['TaskGroup']=sg(env,settings,'Receive only ALB',[{'IpProtocol':'tcp','FromPort':8080,'ToPort':8080,'SourceSecurityGroupId':ref('LoadBalancerGroup')}])
    r['LoadBalancer']={'Type':'AWS::ElasticLoadBalancingV2::LoadBalancer','Properties':{'Name':('ch-'+env['environment_id'][4:])[:32],
        'Subnets':settings['public_subnet_ids'],'SecurityGroups':[ref('LoadBalancerGroup')],'Type':'application','Scheme':'internet-facing','Tags':tags(env)}}
    r['TargetGroup']={'Type':'AWS::ElasticLoadBalancingV2::TargetGroup','Properties':{'Name':('ch-'+env['environment_id'][4:]+'-tg')[:32],'VpcId':settings['vpc_id'],'Port':8080,'Protocol':'HTTP','TargetType':'ip','HealthCheckPath':'/health',
        'TargetGroupAttributes':[{'Key':'deregistration_delay.timeout_seconds','Value':'30'}],'Tags':tags(env)}}
    r['Listener']={'Type':'AWS::ElasticLoadBalancingV2::Listener','Properties':{'LoadBalancerArn':ref('LoadBalancer'),'Port':80,'Protocol':'HTTP','DefaultActions':[{'Type':'forward','TargetGroupArn':ref('TargetGroup')}]}}
    repo=image.split('.amazonaws.com/')[1].split('@')[0]
    r['ExecutionRole']=role(env,settings,'ecs-tasks.amazonaws.com',[
        allow('ecr:GetAuthorizationToken','*'),allow(['ecr:BatchCheckLayerAvailability','ecr:GetDownloadUrlForLayer','ecr:BatchGetImage'],sub('arn:${AWS::Partition}:ecr:${AWS::Region}:${AWS::AccountId}:repository/'+repo)),
        allow(['logs:CreateLogStream','logs:PutLogEvents'],sub('arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:'+name(env,'-container')+':*'))],'execution')
    r['TaskRole']=role(env,settings,'ecs-tasks.amazonaws.com',[allow('s3:ListBucket',att('DataBucket')),allow('s3:GetObject',sub('${DataBucket.Arn}/*'))],'task')
    r['LogGroup']={'Type':'AWS::Logs::LogGroup','Properties':{'LogGroupName':name(env,'-container'),'RetentionInDays':7}}
    r['Cluster']={'Type':'AWS::ECS::Cluster','Properties':{'ClusterName':name(env,'-ecs'),'Tags':tags(env)}}
    r['Task']={'Type':'AWS::ECS::TaskDefinition','Properties':{'Family':name(env,'-task'),'Cpu':'256','Memory':'512','NetworkMode':'awsvpc',
        'RequiresCompatibilities':['FARGATE'],'ExecutionRoleArn':att('ExecutionRole'),'TaskRoleArn':att('TaskRole'),'RuntimePlatform':{'CpuArchitecture':'X86_64','OperatingSystemFamily':'LINUX'},
        'ContainerDefinitions':[{'Name':'app','Image':image,'Essential':True,'ReadonlyRootFilesystem':True,'PortMappings':[{'ContainerPort':8080,'Protocol':'tcp'}],
            'Environment':[{'Name':'DATA_BUCKET','Value':ref('DataBucket')},{'Name':'AWS_DEFAULT_REGION','Value':ref('AWS::Region')},{'Name':'PYTHONDONTWRITEBYTECODE','Value':'1'}],
            'LogConfiguration':{'LogDriver':'awslogs','Options':{'awslogs-group':ref('LogGroup'),'awslogs-region':ref('AWS::Region'),'awslogs-stream-prefix':'app'}}}], 'Tags':tags(env)}}
    r['Service']={'Type':'AWS::ECS::Service','DependsOn':'Listener','Properties':{'ServiceName':name(env,'-service'),'Cluster':ref('Cluster'),'TaskDefinition':ref('Task'),
        'DesiredCount':1,'LaunchType':'FARGATE','PlatformVersion':'1.4.0','EnableECSManagedTags':True,'PropagateTags':'SERVICE','HealthCheckGracePeriodSeconds':120,
        'DeploymentConfiguration':{'DeploymentCircuitBreaker':{'Enable':True,'Rollback':False},'MaximumPercent':100,'MinimumHealthyPercent':0},
        'NetworkConfiguration':{'AwsvpcConfiguration':{'AssignPublicIp':'ENABLED','Subnets':settings['public_subnet_ids'],'SecurityGroups':[ref('TaskGroup')]}},
        'LoadBalancers':[{'ContainerName':'app','ContainerPort':8080,'TargetGroupArn':ref('TargetGroup')}],'Tags':tags(env)}}
    return template(r,{'BucketName':ref('DataBucket'),'Cluster':ref('Cluster'),'Service':att('Service','Name'),'TargetGroupArn':ref('TargetGroup'),'WebsiteUrl':sub('http://${LoadBalancer.DNSName}')})
