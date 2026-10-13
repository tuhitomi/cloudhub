"""Additional template permissions. Environment roles cannot widen the boundary."""
import json
from generate_infra import ref,att,sub,policy,role

def extend(params,r,settings,cf,worker):
    params.update({'PublicSubnet2Id':{'Type':'AWS::EC2::Subnet::Id'},'DbSubnet1Id':{'Type':'AWS::EC2::Subnet::Id'},'DbSubnet2Id':{'Type':'AWS::EC2::Subnet::Id'},
        'PostgresVersion':{'Type':'String','AllowedPattern':'[0-9]+\\.[0-9]+','Description':'Approved RDS PostgreSQL version in the chosen region'},
        'ApprovedImage':{'Type':'String','Default':'','Description':'Reviewed own-account ECR digest for T06; required when enabling T06'}})
    for key in ['VerifiedTemplates','ValidationTemplates']:params[key]['AllowedPattern']='(T0[1-9]|T10)?(,(T0[1-9]|T10))*'
    settings.update(public_subnet_ids=['${PublicSubnetId}','${PublicSubnet2Id}'],db_subnet_ids=['${DbSubnet1Id}','${DbSubnet2Id}'],
        postgres_version='${PostgresVersion}',approved_image='${ApprovedImage}',permissions_boundary='${EnvironmentBoundary}',
        management_table='${Table}',management_table_arn='${Table.Arn}',cognito_client_id='${UserClient}',
        cognito_issuer='https://cognito-idp.${AWS::Region}.amazonaws.com/${UserPool}',backup_restore_role='${BackupRestoreRole.Arn}',
        frontend_origin='https://${FrontendDistribution.DomainName}')
    region={'StringEquals':{'aws:RequestedRegion':ref('AWS::Region')}}
    def arn(service,resource):return sub('arn:${AWS::Partition}:'+service+':${AWS::Region}:${AWS::AccountId}:'+resource)
    own_role=sub('arn:${AWS::Partition}:iam::${AWS::AccountId}:role/cloudhub-env-*')
    own_profile=sub('arn:${AWS::Partition}:iam::${AWS::AccountId}:instance-profile/cloudhub-env-*')
    data_table=arn('dynamodb','table/cloudhub-env-*-data')
    # Tag APIs use the log-group ARN without ':*'; streams add a suffix.
    log_groups=arn('logs','log-group:*cloudhub-env-*')
    # A managed boundary is evaluated in addition to each exact inline application policy.
    boundary=[policy(['ssm:UpdateInstanceInformation','ssmmessages:CreateControlChannel','ssmmessages:CreateDataChannel','ssmmessages:OpenControlChannel','ssmmessages:OpenDataChannel','ecr:GetAuthorizationToken']),
        policy(['ecr:BatchCheckLayerAvailability','ecr:GetDownloadUrlForLayer','ecr:BatchGetImage'],arn('ecr','repository/*')),
        policy(['logs:CreateLogStream','logs:PutLogEvents'],log_groups),policy('dynamodb:Scan',data_table),
        policy('dynamodb:GetItem',att('Table'),{'ForAllValues:StringLike':{'dynamodb:LeadingKeys':[sub('E#${!aws:PrincipalTag/cloudhub:environment}'),sub('U#${!aws:PrincipalTag/cloudhub:owner}')]}}),
        policy('secretsmanager:GetSecretValue',arn('secretsmanager','secret:*'),{'StringEquals':{'secretsmanager:ResourceTag/cloudhub:environment':sub('${!aws:PrincipalTag/cloudhub:environment}')}}),
        policy('s3:ListBucket',sub('arn:${AWS::Partition}:s3:::cloudhub-${AWS::AccountId}-${AWS::Region}-*')),
        policy('s3:GetObject',sub('arn:${AWS::Partition}:s3:::cloudhub-${AWS::AccountId}-${AWS::Region}-*/*'))]
    r['EnvironmentBoundary']={'Type':'AWS::IAM::ManagedPolicy','Properties':{'PolicyDocument':{'Version':'2012-10-17','Statement':boundary}}}
    r['BackupRestoreRole']=role('backup.amazonaws.com',[],[sub('arn:${AWS::Partition}:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForRestores')])
    r['BackupRestoreRole']['Properties'].pop('Policies')
    rds_db=arn('rds','db:cloudhub-env-*');rds_snap=arn('rds','snapshot:cloudhub-rp-*');rds_subnet=arn('rds','subgrp:cloudhub-env-*')
    cf.extend([
        policy(['ec2:RunInstances'],[sub('arn:${AWS::Partition}:ec2:${AWS::Region}:${AWS::AccountId}:subnet/${PublicSubnet2Id}')],region),
        policy(['ec2:CreateLaunchTemplate','ec2:DeleteLaunchTemplate','ec2:GetLaunchTemplateData'],arn('ec2','launch-template/*'),region),
        policy('iam:CreateRole',own_role,{'ArnEquals':{'iam:PermissionsBoundary':ref('EnvironmentBoundary')}}),
        policy(['iam:DeleteRole','iam:GetRole','iam:PutRolePolicy','iam:GetRolePolicy','iam:DeleteRolePolicy','iam:ListRolePolicies','iam:TagRole','iam:UntagRole'],own_role),
        policy(['iam:CreateInstanceProfile','iam:DeleteInstanceProfile','iam:GetInstanceProfile','iam:AddRoleToInstanceProfile','iam:RemoveRoleFromInstanceProfile'],own_profile),
        policy('iam:PassRole',own_role,{'StringEquals':{'iam:PassedToService':['ec2.amazonaws.com','lambda.amazonaws.com','ecs-tasks.amazonaws.com','redshift.amazonaws.com']}}),
        policy('iam:CreateServiceLinkedRole','*',{'StringEquals':{'iam:AWSServiceName':['autoscaling.amazonaws.com','elasticloadbalancing.amazonaws.com','ecs.amazonaws.com','rds.amazonaws.com','redshift-serverless.amazonaws.com']}}),
        policy(['rds:CreateDBInstance','rds:RestoreDBInstanceFromDBSnapshot','rds:DeleteDBInstance','rds:ModifyDBInstance','rds:AddTagsToResource','rds:ListTagsForResource'],[rds_db,rds_snap,rds_subnet]),
        policy(['rds:CreateDBSubnetGroup','rds:DeleteDBSubnetGroup','rds:ModifyDBSubnetGroup'],rds_subnet),
        policy(['rds:DescribeDBInstances','rds:DescribeDBSubnetGroups','rds:DescribeDBSnapshots'],condition=region),
        policy(['secretsmanager:CreateSecret','secretsmanager:TagResource','secretsmanager:DescribeSecret'],arn('secretsmanager','secret:rds!*')),
        policy(['autoscaling:CreateAutoScalingGroup','autoscaling:UpdateAutoScalingGroup','autoscaling:DeleteAutoScalingGroup','autoscaling:PutScalingPolicy','autoscaling:DeletePolicy','autoscaling:CreateOrUpdateTags'],arn('autoscaling','autoScalingGroup:*:autoScalingGroupName/cloudhub-env-*')),
        policy(['autoscaling:DescribeAutoScalingGroups','autoscaling:DescribePolicies','autoscaling:DescribeScalingActivities'],condition=region),
        policy(['elasticloadbalancing:CreateLoadBalancer','elasticloadbalancing:CreateTargetGroup','elasticloadbalancing:CreateListener','elasticloadbalancing:AddTags'],condition=region),
        policy(['elasticloadbalancing:DeleteLoadBalancer','elasticloadbalancing:DeleteTargetGroup','elasticloadbalancing:DeleteListener','elasticloadbalancing:ModifyTargetGroup','elasticloadbalancing:ModifyTargetGroupAttributes','elasticloadbalancing:ModifyLoadBalancerAttributes','elasticloadbalancing:ModifyListener','elasticloadbalancing:RegisterTargets','elasticloadbalancing:DeregisterTargets'],[arn('elasticloadbalancing','loadbalancer/app/ch-*/*'),arn('elasticloadbalancing','targetgroup/ch-*/*'),arn('elasticloadbalancing','listener/app/ch-*/*/*')]),
        policy('elasticloadbalancing:Describe*',condition=region),
        policy(['cloudwatch:PutMetricAlarm','cloudwatch:DeleteAlarms','cloudwatch:DescribeAlarms'],arn('cloudwatch','alarm:TargetTracking-cloudhub-env-*')),
        policy(['elasticfilesystem:CreateFileSystem','elasticfilesystem:DescribeFileSystems','elasticfilesystem:DescribeMountTargets','elasticfilesystem:DescribeMountTargetSecurityGroups','elasticfilesystem:DescribeBackupPolicy','elasticfilesystem:TagResource','elasticfilesystem:ListTagsForResource','elasticfilesystem:CreateMountTarget','elasticfilesystem:DeleteMountTarget','elasticfilesystem:DeleteFileSystem','elasticfilesystem:PutBackupPolicy'],condition=region),
        policy(['dynamodb:CreateTable','dynamodb:DescribeTable','dynamodb:DeleteTable','dynamodb:TagResource','dynamodb:ListTagsOfResource'],data_table),
        policy(['lambda:CreateFunction','lambda:GetFunction','lambda:GetFunctionConfiguration','lambda:DeleteFunction','lambda:AddPermission','lambda:RemovePermission','lambda:GetPolicy','lambda:TagResource','lambda:UntagResource','lambda:PutFunctionConcurrency','lambda:DeleteFunctionConcurrency'],arn('lambda','function:cloudhub-env-*-data')),
        policy(['apigateway:GET','apigateway:POST','apigateway:DELETE','apigateway:PATCH','apigateway:TagResource'],sub('arn:${AWS::Partition}:apigateway:${AWS::Region}::/apis*')),
        # CloudFormation's HTTP API tag requests use a URL-encoded API ARN.
        # Keep this grant on v2 API tags in the configured region.
        policy(['apigateway:GET','apigateway:POST','apigateway:DELETE'],sub('arn:${AWS::Partition}:apigateway:${AWS::Region}::/tags/arn%3A${AWS::Partition}%3Aapigateway%3A${AWS::Region}%3A%3A%2Fv2%2Fapis%2F*')),
        policy(['logs:CreateLogGroup','logs:DeleteLogGroup','logs:PutRetentionPolicy','logs:TagResource','logs:ListTagsForResource'],log_groups),
        policy('logs:DescribeLogGroups',condition=region),
        policy(['ecs:CreateCluster','ecs:DeleteCluster','ecs:CreateService','ecs:DeleteService','ecs:UpdateService','ecs:DescribeClusters','ecs:DescribeServices','ecs:RegisterTaskDefinition','ecs:DeregisterTaskDefinition','ecs:DescribeTaskDefinition','ecs:TagResource','ecs:UntagResource','ecs:ListTagsForResource'],condition=region),
        policy(['redshift-serverless:CreateNamespace','redshift-serverless:DeleteNamespace','redshift-serverless:UpdateNamespace','redshift-serverless:GetNamespace','redshift-serverless:CreateWorkgroup','redshift-serverless:DeleteWorkgroup','redshift-serverless:UpdateWorkgroup','redshift-serverless:GetWorkgroup','redshift-serverless:RestoreFromSnapshot','redshift-serverless:GetSnapshot','redshift-serverless:TagResource','redshift-serverless:ListTagsForResource'],condition=region)
    ])
    # RDS must describe aws/secretsmanager when it manages the master password.
    # AWS-reserved aliases narrow this metadata-only permission to default keys.
    cf.append(policy('kms:DescribeKey',arn('kms','key/*'),{
        'StringEquals':{'aws:RequestedRegion':ref('AWS::Region')},
        'ForAnyValue:StringEquals':{'kms:ResourceAliases':['alias/aws/rds','alias/aws/secretsmanager']}}))
    # ALB validates security-group/VPC associations through this metadata API.
    cf.append(policy('ec2:GetSecurityGroupsForVpc',
        sub('arn:${AWS::Partition}:ec2:${AWS::Region}:${AWS::AccountId}:vpc/${VpcId}'),region))
    # EFS creates requester-managed ENIs. New ENIs have no resource tags yet;
    # authorization also requires the two exact approved subnet ARNs.
    cf.append(policy('ec2:CreateNetworkInterface',[
        arn('ec2','network-interface/*'),arn('ec2','security-group/*'),
        sub('arn:${AWS::Partition}:ec2:${AWS::Region}:${AWS::AccountId}:subnet/${PublicSubnetId}'),
        sub('arn:${AWS::Partition}:ec2:${AWS::Region}:${AWS::AccountId}:subnet/${PublicSubnet2Id}')],region))
    cf.append(policy('ec2:DeleteNetworkInterface',arn('ec2','network-interface/*'),{
        'StringEquals':{'aws:RequestedRegion':ref('AWS::Region')},
        'ArnEquals':{'ec2:Vpc':sub('arn:${AWS::Partition}:ec2:${AWS::Region}:${AWS::AccountId}:vpc/${VpcId}'),
            'ec2:Subnet':[
            sub('arn:${AWS::Partition}:ec2:${AWS::Region}:${AWS::AccountId}:subnet/${PublicSubnetId}'),
            sub('arn:${AWS::Partition}:ec2:${AWS::Region}:${AWS::AccountId}:subnet/${PublicSubnet2Id}')]}}))
    # The EFS CloudFormation delete handler checks replication before deleting.
    cf.append(policy('elasticfilesystem:DescribeReplicationConfigurations',
        arn('elasticfilesystem','file-system/*'),region))
    worker.extend([
        policy('cloudformation:UpdateStack',arn('cloudformation','stack/cloudhub-env-*/*')),
        policy(['rds:DescribeDBInstances','rds:DescribeDBSnapshots','rds:DescribeDBSubnetGroups'],condition=region),
        policy(['rds:CreateDBSnapshot','rds:DeleteDBSnapshot','rds:AddTagsToResource','rds:ListTagsForResource'],[rds_db,rds_snap]),
        policy(['autoscaling:DescribeAutoScalingGroups','autoscaling:DescribePolicies','elasticloadbalancing:Describe*'],condition=region),
        policy('cloudwatch:DescribeAlarms',arn('cloudwatch','alarm:TargetTracking-cloudhub-env-*')),
        policy(['iam:GetRole'],own_role),policy('iam:GetInstanceProfile',own_profile),
        policy(['secretsmanager:DescribeSecret','secretsmanager:TagResource'],arn('secretsmanager','secret:*'),region),
        policy('secretsmanager:GetSecretValue',arn('secretsmanager','secret:*'),{'StringEquals':{'secretsmanager:ResourceTag/cloudhub:managed':'true'}}),
        policy(['elasticfilesystem:DescribeFileSystems','elasticfilesystem:DescribeMountTargets','elasticfilesystem:ListTagsForResource'],condition=region),
        policy(['backup:StartRestoreJob','backup:DescribeRestoreJob','backup:ListRecoveryPointsByBackupVault','backup:ListTags'],condition=region),
        policy('iam:PassRole',att('BackupRestoreRole'),{'StringEquals':{'iam:PassedToService':'backup.amazonaws.com'}}),
        policy(['dynamodb:DescribeTable','dynamodb:ListTagsOfResource','dynamodb:Scan','dynamodb:GetItem','dynamodb:PutItem','dynamodb:UpdateItem','dynamodb:DeleteItem','dynamodb:ConditionCheckItem'],data_table),
        policy(['lambda:GetFunction','lambda:GetFunctionConfiguration','lambda:GetPolicy'],arn('lambda','function:cloudhub-env-*-data')),
        policy('apigateway:GET',sub('arn:${AWS::Partition}:apigateway:${AWS::Region}::/apis*')),
        policy(['logs:DescribeLogGroups'],condition=region),
        policy(['ecs:DescribeClusters','ecs:DescribeServices','ecs:DescribeTasks','ecs:ListTasks','ecs:DescribeTaskDefinition','ecs:DeleteTaskDefinitions'],condition=region),
        policy(['redshift-serverless:GetNamespace','redshift-serverless:GetWorkgroup','redshift-serverless:GetSnapshot','redshift-serverless:ListSnapshots','redshift-serverless:ListTagsForResource','redshift-serverless:CreateSnapshot','redshift-serverless:DeleteSnapshot','redshift-serverless:CreateUsageLimit','redshift-serverless:ListUsageLimits','redshift-serverless:GetUsageLimit','redshift-serverless:DeleteUsageLimit'],condition=region),
        policy(['redshift-data:ExecuteStatement','redshift-data:BatchExecuteStatement','redshift-data:DescribeStatement','redshift-data:GetStatementResult','redshift-data:CancelStatement'],condition=region)
    ])
    worker.append(policy('redshift-serverless:UpdateNamespace',arn('redshift-serverless','namespace/*'),
        {'StringEquals':{'aws:RequestedRegion':ref('AWS::Region'),'aws:ResourceTag/cloudhub:managed':'true'}}))
    worker.append(policy('iam:PassRole',own_role,{'StringEquals':{'iam:PassedToService':'redshift.amazonaws.com'}}))
    # Managed policies have a 6,144-character size limit, independent of formatting.
    chunks=[];chunk=[]
    for stmt in cf:
        if len(json.dumps({'Version':'2012-10-17','Statement':chunk+[stmt]},separators=(',',':')))>5800:
            chunks.append(chunk);chunk=[]
        chunk.append(stmt)
    if chunk:chunks.append(chunk)
    r['EnvironmentCfRole']['Properties'].pop('Policies')
    for i,items in enumerate(chunks):r['EnvironmentPolicy'+str(i)]={'Type':'AWS::IAM::ManagedPolicy','Properties':{'Roles':[ref('EnvironmentCfRole')],'PolicyDocument':{'Version':'2012-10-17','Statement':items}}}
