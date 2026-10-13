"""Common bounded CF lifecycle and resource reconciliation for additional processors."""
import json
from datetime import datetime,timezone
from .aws_driver import AWSDriver,code
from .errors import HubError

SOURCE_TYPES={'AWS::RDS::DBInstance','AWS::EFS::FileSystem','AWS::DynamoDB::Table','AWS::RedshiftServerless::Namespace','AWS::S3::Bucket'}

class StackDriver(AWSDriver):
    services=('cloudformation','ec2','ssm','scheduler','rds','autoscaling','elbv2','iam','secretsmanager','efs','dynamodb','lambda','apigatewayv2','ecs','logs','s3','cloudwatch','redshift-serverless','redshift-data')
    def __init__(self,settings,clients=None):
        self.settings,self.clients=settings,clients or {}
    def __getattr__(self,key):
        if key not in self.services:raise AttributeError(key)
        if key not in self.clients:
            import boto3
            from botocore.config import Config
            self.clients[key]=boto3.client(key,config=Config(connect_timeout=2,read_timeout=5,retries={'total_max_attempts':2}))
        return self.clients[key]
    def create(self,env,op,recovery):
        existing=self.assert_stack(env)
        if existing:
            if existing['StackStatus'] in {'CREATE_FAILED','UPDATE_FAILED'}:
                if existing.get('DisableRollback') is not True:
                    raise HubError('LEGACY_STACK_RETRY_UNAVAILABLE',
                        'Stack cũ không hỗ trợ thử lại. Giữ dữ liệu; quản trị cần sao lưu và thu hồi trước khi tạo mới.',409)
                # Retry only the saved template on this exact owned stack.
                # Preserve successful resources and data if another step fails.
                self.cloudformation.update_stack(StackName=self.stack_name(env),UsePreviousTemplate=True,
                    DisableRollback=True,Capabilities=['CAPABILITY_NAMED_IAM'],
                    ClientRequestToken=op['operation_id']+'-g'+str(op.get('generation',0)),
                    RoleARN=self.settings['cf_role_arn'])
            return
        self.cloudformation.create_stack(StackName=self.stack_name(env),TemplateBody=json.dumps(self.build_template(env,recovery)),
            Capabilities=['CAPABILITY_NAMED_IAM'],ClientRequestToken=op['operation_id'],DisableRollback=True,RoleARN=self.settings['cf_role_arn'],
            Tags=[{'Key':'cloudhub:environment','Value':env['environment_id']},{'Key':'cloudhub:owner','Value':env['owner_id']}])
    def created(self,env):
        stack=self.assert_stack(env)
        if not stack:raise RuntimeError('Stack missing during create')
        self.inventory(env)
        if stack['StackStatus'] in {'CREATE_FAILED','UPDATE_FAILED','ROLLBACK_FAILED','ROLLBACK_COMPLETE'}:raise HubError('CREATE_FAILED',stack.get('StackStatusReason',stack['StackStatus']),409)
        if stack['StackStatus'] not in {'CREATE_COMPLETE','UPDATE_COMPLETE'}:return False
        env['outputs']={v['OutputKey']:v['OutputValue'] for v in stack.get('Outputs',[])}
        self.apply_outputs(env)
        return True
    def inventory(self,env):
        rows={(r['type'],r['id']):r for r in env.get('resources',[])}
        if self.assert_stack(env):
            for page in self.cloudformation.get_paginator('list_stack_resources').paginate(StackName=self.stack_name(env)):
                for item in page['StackResourceSummaries']:
                    if item.get('PhysicalResourceId'):
                        rows[(item['ResourceType'],item['PhysicalResourceId'])]={'type':item['ResourceType'],'id':item['PhysicalResourceId'],'logical_id':item['LogicalResourceId']}
        # ASG instances and root volumes/ENIs are generated outside root CF resource IDs.
        filters=[{'Name':'tag:cloudhub:environment','Values':[env['environment_id']]},{'Name':'tag:cloudhub:owner','Values':[env['owner_id']]}]
        pages=self.ec2.get_paginator('describe_instances').paginate(Filters=filters) if env['template_id'] in {'T03','T04','T08'} else []
        for page in pages:
            for reservation in page['Reservations']:
                for instance in reservation['Instances']:
                    if instance['State']['Name']=='terminated':continue
                    rows[('AWS::EC2::Instance',instance['InstanceId'])]={'type':'AWS::EC2::Instance','id':instance['InstanceId']}
                    for volume in instance.get('BlockDeviceMappings',[]):
                        if volume.get('Ebs'):rows[('AWS::EC2::Volume',volume['Ebs']['VolumeId'])]={'type':'AWS::EC2::Volume','id':volume['Ebs']['VolumeId']}
                    for interface in instance.get('NetworkInterfaces',[]):rows[('AWS::EC2::NetworkInterface',interface['NetworkInterfaceId'])]={'type':'AWS::EC2::NetworkInterface','id':interface['NetworkInterfaceId']}
        # Service-created ENIs (ALB/EFS/Fargate/Redshift) also attach to this stack's
        # private, per-environment security groups. Save their IDs before deletion.
        groups=[v['id'] for v in rows.values() if v['type']=='AWS::EC2::SecurityGroup']
        if groups:
            for page in self.ec2.get_paginator('describe_network_interfaces').paginate(Filters=[{'Name':'group-id','Values':groups}]):
                for interface in page['NetworkInterfaces']:
                    rid=interface['NetworkInterfaceId']
                    rows[('AWS::EC2::NetworkInterface',rid)]={'type':'AWS::EC2::NetworkInterface','id':rid}
        env['resources']=list(rows.values())
        api_id=next((r['id'] for r in env['resources'] if r['type']=='AWS::ApiGatewayV2::Api'),None)
        function=next((r['id'] for r in env['resources'] if r['type']=='AWS::Lambda::Function'),None)
        asg=next((r['id'] for r in env['resources'] if r['type']=='AWS::AutoScaling::AutoScalingGroup'),None)
        for item in env['resources']:
            if item['type'].startswith('AWS::ApiGatewayV2::') and item['type']!='AWS::ApiGatewayV2::Api':item['api_id']=api_id
            if item['type']=='AWS::Lambda::Permission':item['function_name']=function
            if item['type']=='AWS::AutoScaling::ScalingPolicy':item['group_name']=asg
        if asg:
            for page in self.autoscaling.get_paginator('describe_policies').paginate(AutoScalingGroupName=asg):
                for policy in page['ScalingPolicies']:
                    for alarm in policy.get('Alarms',[]):
                        item={'type':'AWS::CloudWatch::Alarm','id':alarm['AlarmName']}
                        if not any(v['type']==item['type'] and v['id']==item['id'] for v in env['resources']):env['resources'].append(item)
        env['source_ids']=[r['id'] for r in env['resources'] if r['type'] in SOURCE_TYPES]
        self.discover(env)
        return env['resources']
    def apply_outputs(self,env):pass
    def discover(self,env):pass
    def resume(self,env):pass
    def tag_secret(self,env,arn):
        current=self.secretsmanager.describe_secret(SecretId=arn)
        tags={v['Key']:v['Value'] for v in current.get('Tags',[])}
        if tags.get('cloudhub:environment') and tags['cloudhub:environment']!=env['environment_id']:
            raise HubError('RESOURCE_SCOPE_MISMATCH','Secret đã thuộc môi trường khác.',409)
        if tags.get('cloudhub:owner') and tags['cloudhub:owner']!=env['owner_id']:
            raise HubError('RESOURCE_SCOPE_MISMATCH','Secret đã thuộc người dùng khác.',409)
        if tags.get('cloudhub:environment')!=env['environment_id']:
            self.secretsmanager.tag_resource(SecretId=arn,Tags=[{'Key':'cloudhub:environment','Value':env['environment_id']},{'Key':'cloudhub:owner','Value':env['owner_id']},{'Key':'cloudhub:managed','Value':'true'}])
    def delete(self,env,op):
        self.inventory(env)
        if not super().delete(env,op):return False
        for item in env.get('resources',[]):
            if item['type']!='AWS::ECS::TaskDefinition':continue
            try:detail=self.ecs.describe_task_definition(taskDefinition=item['id'])['taskDefinition']
            except Exception as exc:
                if code(exc)=='ClientException' and 'Unable to describe task definition' in str(exc):continue
                raise
            if detail['status']=='INACTIVE':self.ecs.delete_task_definitions(taskDefinitions=[item['id']])
            return False
        return True
    def _exists(self,item):
        typ,rid=item['type'],item['id']
        if typ=='AWS::EC2::Instance':
            response=self.ec2.describe_instances(InstanceIds=[rid])
            return any(v['State']['Name']!='terminated' for r in response['Reservations'] for v in r['Instances'])
        specs={
          'AWS::EC2::Volume':('ec2','describe_volumes','VolumeIds','Volumes'),
          'AWS::EC2::NetworkInterface':('ec2','describe_network_interfaces','NetworkInterfaceIds','NetworkInterfaces'),
          'AWS::EC2::SecurityGroup':('ec2','describe_security_groups','GroupIds','SecurityGroups')}
        if typ in specs:
            client,method,param,result=specs[typ];return bool(getattr(getattr(self,client),method)(**{param:[rid]})[result])
        if typ=='AWS::EC2::LaunchTemplate':return bool(self.ec2.describe_launch_templates(LaunchTemplateIds=[rid])['LaunchTemplates'])
        if typ=='AWS::RDS::DBInstance':return bool(self.rds.describe_db_instances(DBInstanceIdentifier=rid)['DBInstances'])
        if typ=='AWS::RDS::DBSubnetGroup':return bool(self.rds.describe_db_subnet_groups(DBSubnetGroupName=rid)['DBSubnetGroups'])
        if typ=='AWS::AutoScaling::AutoScalingGroup':return bool(self.autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[rid])['AutoScalingGroups'])
        if typ=='AWS::AutoScaling::ScalingPolicy':
            group=item.get('group_name')
            if not group:return None
            if not self.autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[group])['AutoScalingGroups']:return False
            return bool(self.autoscaling.describe_policies(AutoScalingGroupName=group,PolicyNames=[rid])['ScalingPolicies'])
        if typ=='AWS::CloudWatch::Alarm':return bool(self.cloudwatch.describe_alarms(AlarmNames=[rid])['MetricAlarms'])
        if typ=='AWS::ElasticLoadBalancingV2::LoadBalancer':return bool(self.elbv2.describe_load_balancers(LoadBalancerArns=[rid])['LoadBalancers'])
        if typ=='AWS::ElasticLoadBalancingV2::TargetGroup':return bool(self.elbv2.describe_target_groups(TargetGroupArns=[rid])['TargetGroups'])
        if typ=='AWS::ElasticLoadBalancingV2::Listener':return bool(self.elbv2.describe_listeners(ListenerArns=[rid])['Listeners'])
        if typ=='AWS::IAM::Role':return bool(self.iam.get_role(RoleName=rid)['Role'])
        if typ=='AWS::IAM::InstanceProfile':return bool(self.iam.get_instance_profile(InstanceProfileName=rid)['InstanceProfile'])
        if typ=='AWS::SecretsManager::Secret':return bool(self.secretsmanager.describe_secret(SecretId=rid))
        if typ=='AWS::EFS::FileSystem':return bool(self.efs.describe_file_systems(FileSystemId=rid)['FileSystems'])
        if typ=='AWS::EFS::MountTarget':return bool(self.efs.describe_mount_targets(MountTargetId=rid)['MountTargets'])
        if typ=='AWS::DynamoDB::Table':return bool(self.dynamodb.describe_table(TableName=rid)['Table'])
        if typ=='AWS::Lambda::Function':return bool(getattr(self,'lambda').get_function(FunctionName=rid))
        if typ=='AWS::Lambda::Permission':
            if not item.get('function_name'):return None
            detail=getattr(self,'lambda').get_policy(FunctionName=item['function_name'])
            return any(s['Sid']==rid for s in json.loads(detail['Policy'])['Statement'])
        if typ=='AWS::ApiGatewayV2::Api':return bool(self.apigatewayv2.get_api(ApiId=rid))
        if typ in {'AWS::ApiGatewayV2::Stage','AWS::ApiGatewayV2::Route','AWS::ApiGatewayV2::Integration','AWS::ApiGatewayV2::Authorizer'}:
            if not item.get('api_id'):return None
            methods={'AWS::ApiGatewayV2::Stage':('get_stage','StageName'),'AWS::ApiGatewayV2::Route':('get_route','RouteId'),
                'AWS::ApiGatewayV2::Integration':('get_integration','IntegrationId'),'AWS::ApiGatewayV2::Authorizer':('get_authorizer','AuthorizerId')}
            method,param=methods[typ]
            return bool(getattr(self.apigatewayv2,method)(ApiId=item['api_id'],**{param:('$default' if param=='StageName' else rid.split('|')[-1])}))
        if typ=='AWS::Logs::LogGroup':return any(v['logGroupName']==rid for v in self.logs.describe_log_groups(logGroupNamePrefix=rid)['logGroups'])
        if typ=='AWS::ECS::Cluster':return any(v['status']!='INACTIVE' for v in self.ecs.describe_clusters(clusters=[rid])['clusters'])
        if typ=='AWS::ECS::Service':
            if '/' not in rid:return None
            parts=rid.split('/');return any(v['status']!='INACTIVE' for v in self.ecs.describe_services(cluster=parts[-2],services=[rid])['services'])
        if typ=='AWS::ECS::TaskDefinition':
            try:return bool(self.ecs.describe_task_definition(taskDefinition=rid)['taskDefinition'])
            except Exception as exc:
                if code(exc)=='ClientException' and 'Unable to describe task definition' in str(exc):return False
                raise
        if typ=='AWS::RedshiftServerless::Namespace':return bool(getattr(self,'redshift-serverless').get_namespace(namespaceName=rid))
        if typ=='AWS::RedshiftServerless::Workgroup':return bool(getattr(self,'redshift-serverless').get_workgroup(workgroupName=rid))
        return None
    def audit(self,env,backups):
        report={k:[] for k in ['deleted','retained','residual','unknown','billable']}
        report['coverage']='Saved environment CF resources + generated EC2/EBS/ENI; processor retained backups; unrecognized resources remain unknown.'
        missing={'InvalidInstanceID.NotFound','InvalidVolume.NotFound','InvalidNetworkInterfaceID.NotFound','InvalidGroup.NotFound',
            'InvalidLaunchTemplateId.NotFound','DBInstanceNotFound','DBSubnetGroupNotFoundFault','LoadBalancerNotFound','TargetGroupNotFound','ListenerNotFound',
            'NoSuchEntity','ResourceNotFoundException','FileSystemNotFound','MountTargetNotFound','NotFoundException',
            'DBInstanceNotFoundFault','DBSubnetGroupNotFound'}
        for item in env.get('resources',[]):
            try:
                exists=self._exists(item)
                report['unknown' if exists is None else 'residual' if exists else 'deleted'].append(item)
            except Exception as exc:
                report['deleted' if code(exc) in missing else 'unknown'].append({**item,**({} if code(exc) in missing else {'error':code(exc) or type(exc).__name__})})
        stack=self.assert_stack(env)
        if stack and stack['StackStatus']!='DELETE_COMPLETE':report['residual'].append({'type':'AWS::CloudFormation::Stack','id':self.stack_name(env)})
        self.audit_backups(env,backups,report)
        nonbilling={'AWS::IAM::Role','AWS::IAM::InstanceProfile','AWS::EC2::SecurityGroup','AWS::CloudFormation::Stack','AWS::AutoScaling::ScalingPolicy'}
        report['billable']=[v for v in report['residual']+report['retained']+report['unknown'] if v['type'] not in nonbilling]
        return report
    def audit_backups(self,env,backups,report):pass
    def active_instances(self,env):
        rows=[]
        for page in self.ec2.get_paginator('describe_instances').paginate(Filters=[{'Name':'tag:cloudhub:environment','Values':[env['environment_id']]},
            {'Name':'tag:cloudhub:owner','Values':[env['owner_id']]},{'Name':'instance-state-name','Values':['running']}]):
            rows += [v for r in page['Reservations'] for v in r['Instances']]
        return rows
