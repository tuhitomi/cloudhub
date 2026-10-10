"""Generate JSON SAM (JSON avoids custom YAML parsing locally). No AWS calls."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
def ref(x): return {"Ref": x}
def sub(x): return {"Fn::Sub": x}
def att(x, y="Arn"): return {"Fn::GetAtt": [x, y]}
def policy(actions, resource="*", condition=None):
    p = {"Effect": "Allow", "Action": actions, "Resource": resource}
    if condition: p["Condition"] = condition
    return p
def role(service, statements, managed=None):
    p = {"AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": service}, "Action": "sts:AssumeRole"}]},
         "Policies": [{"PolicyName": "cloudhub", "PolicyDocument": {"Version": "2012-10-17", "Statement": statements}}]}
    if managed: p["ManagedPolicyArns"] = managed
    return {"Type": "AWS::IAM::Role", "Properties": p}

def main():
    r = {}
    params = {
        "VpcId": {"Type": "AWS::EC2::VPC::Id"}, "PublicSubnetId": {"Type": "AWS::EC2::Subnet::Id"},
        "AvailabilityZone": {"Type": "AWS::EC2::AvailabilityZone::Name"}, "AmiId": {"Type": "AWS::EC2::Image::Id"},
        "SshCidr": {"Type": "String", "AllowedPattern": "([0-9]{1,3}\\.){3}[0-9]{1,3}/(2[4-9]|3[0-2])", "Description": "Approved IPv4 network /24 or narrower for SSH, configured by operator"},
        "PricesJson": {"Type": "String", "Default": '{"approved":false}'},
        "BudgetMilliusd": {"Type": "Number", "Default": 0, "MinValue": 0},
        "VerifiedTemplates": {"Type": "String", "Default": "", "AllowedPattern": "(T01|T02|T05)?(,(T01|T02|T05))*"},
        "ValidationTemplates": {"Type": "String", "Default": "", "AllowedPattern": "(T01|T02|T05)?(,(T01|T02|T05))*"},
        "UserLimit": {"Type": "Number", "Default": 2, "MinValue": 1, "MaxValue": 4},
        "TotalLimit": {"Type": "Number", "Default": 4, "MinValue": 1, "MaxValue": 8}}
    r["Table"] = {"Type": "AWS::DynamoDB::Table", "DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain", "Properties": {
        "BillingMode": "PAY_PER_REQUEST", "AttributeDefinitions": [{"AttributeName": x, "AttributeType": "S"} for x in ["pk", "owner_id", "owner_sort"]],
        "KeySchema": [{"AttributeName": "pk", "KeyType": "HASH"}],
        "GlobalSecondaryIndexes": [{"IndexName": "by_owner", "KeySchema": [{"AttributeName": "owner_id", "KeyType": "HASH"}, {"AttributeName": "owner_sort", "KeyType": "RANGE"}], "Projection": {"ProjectionType": "ALL"}}],
        "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True}, "SSESpecification": {"SSEEnabled": True}}}
    r["FrontendBucket"] = {"Type": "AWS::S3::Bucket", "DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain", "Properties": {
        "PublicAccessBlockConfiguration": {"BlockPublicAcls": True, "BlockPublicPolicy": True, "IgnorePublicAcls": True, "RestrictPublicBuckets": True},
        "BucketEncryption": {"ServerSideEncryptionConfiguration": [{"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]},
        "OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}}}
    r["OriginAccess"] = {"Type": "AWS::CloudFront::OriginAccessControl", "Properties": {"OriginAccessControlConfig": {"Name": sub("${AWS::StackName}-oac"), "OriginAccessControlOriginType": "s3", "SigningBehavior": "always", "SigningProtocol": "sigv4"}}}
    r["FrontendDistribution"] = {"Type": "AWS::CloudFront::Distribution", "Properties": {"DistributionConfig": {
        "Enabled": True, "DefaultRootObject": "index.html", "HttpVersion": "http2", "PriceClass": "PriceClass_100",
        "Origins": [{"Id": "private-s3", "DomainName": att("FrontendBucket", "RegionalDomainName"), "S3OriginConfig": {"OriginAccessIdentity": ""}, "OriginAccessControlId": ref("OriginAccess")}],
        "DefaultCacheBehavior": {"TargetOriginId": "private-s3", "ViewerProtocolPolicy": "redirect-to-https", "AllowedMethods": ["GET", "HEAD"], "CachedMethods": ["GET", "HEAD"], "Compress": True, "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6", "ResponseHeadersPolicyId": "67f7725c-6f97-4210-82d7-5512b31e9d03"},
        "ViewerCertificate": {"CloudFrontDefaultCertificate": True}}}}
    r["FrontendBucketPolicy"] = {"Type": "AWS::S3::BucketPolicy", "Properties": {"Bucket": ref("FrontendBucket"), "PolicyDocument": {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Principal": {"Service": "cloudfront.amazonaws.com"}, "Action": "s3:GetObject", "Resource": sub("${FrontendBucket.Arn}/*"), "Condition": {"StringEquals": {"AWS:SourceArn": sub("arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:distribution/${FrontendDistribution}")}}},
        {"Effect": "Deny", "Principal": "*", "Action": "s3:*", "Resource": [att("FrontendBucket"), sub("${FrontendBucket.Arn}/*")], "Condition": {"Bool": {"aws:SecureTransport": "false"}}}]}}}
    r["UserPool"] = {"Type": "AWS::Cognito::UserPool", "Properties": {"UsernameAttributes": ["email"], "AutoVerifiedAttributes": ["email"], "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True}, "Policies": {"PasswordPolicy": {"MinimumLength": 12, "RequireLowercase": True, "RequireUppercase": True, "RequireNumbers": True, "RequireSymbols": True}}}}
    r["AdminGroup"] = {"Type": "AWS::Cognito::UserPoolGroup", "Properties": {"GroupName": "admin", "UserPoolId": ref("UserPool")}}
    r["ResourceServer"] = {"Type": "AWS::Cognito::UserPoolResourceServer", "Properties": {"Identifier": "cloudhub", "Name": "CloudHub", "UserPoolId": ref("UserPool"), "Scopes": [{"ScopeName": "use", "ScopeDescription": "CloudHub operations"}]}}
    r["UserClient"] = {"Type": "AWS::Cognito::UserPoolClient", "DependsOn": "ResourceServer", "Properties": {"UserPoolId": ref("UserPool"), "GenerateSecret": False, "AllowedOAuthFlowsUserPoolClient": True, "AllowedOAuthFlows": ["code"], "AllowedOAuthScopes": ["openid", "email", "cloudhub/use"], "SupportedIdentityProviders": ["COGNITO"], "CallbackURLs": [sub("https://${FrontendDistribution.DomainName}/")], "LogoutURLs": [sub("https://${FrontendDistribution.DomainName}/")], "PreventUserExistenceErrors": "ENABLED", "EnableTokenRevocation": True, "AccessTokenValidity": 60, "TokenValidityUnits": {"AccessToken": "minutes"}}}
    r["UserDomain"] = {"Type": "AWS::Cognito::UserPoolDomain", "Properties": {"Domain": sub("${AWS::StackName}-${AWS::AccountId}"), "UserPoolId": ref("UserPool")}}
    r["HttpApi"] = {"Type": "AWS::Serverless::HttpApi", "Properties": {"StageName": "$default",
        "CorsConfiguration": {"AllowOrigins": [sub("https://${FrontendDistribution.DomainName}")], "AllowHeaders": ["Authorization", "Content-Type", "Idempotency-Key"], "AllowMethods": ["GET", "POST", "OPTIONS"], "MaxAge": 300},
        "Auth": {"DefaultAuthorizer": "CognitoJwt", "Authorizers": {"CognitoJwt": {"IdentitySource": "$request.header.Authorization", "JwtConfiguration": {"issuer": sub("https://cognito-idp.${AWS::Region}.amazonaws.com/${UserPool}"), "audience": [ref("UserClient")]}, "AuthorizationScopes": ["cloudhub/use"]}}}}}
    r["SessionRole"] = role("ec2.amazonaws.com", [], [sub("arn:${AWS::Partition}:iam::aws:policy/AmazonSSMManagedInstanceCore")])
    # Empty inline statements are not valid: SSM managed policy is sufficient.
    r["SessionRole"]["Properties"].pop("Policies")
    r["SessionProfile"] = {"Type": "AWS::IAM::InstanceProfile", "Properties": {"Roles": [ref("SessionRole")]}}
    region_cond = {"StringEquals": {"aws:RequestedRegion": ref("AWS::Region")}}
    managed_cond = {"StringEquals": {"aws:RequestedRegion": ref("AWS::Region"), "ec2:ResourceTag/cloudhub:managed": "true"}}
    r["EnvironmentCfRole"] = role("cloudformation.amazonaws.com", [
        policy(["ec2:Describe*"], condition=region_cond),
        policy(["ec2:RunInstances"], [sub("arn:${AWS::Partition}:ec2:${AWS::Region}::image/${AmiId}"), sub("arn:${AWS::Partition}:ec2:${AWS::Region}:${AWS::AccountId}:subnet/${PublicSubnetId}")] + [sub("arn:${AWS::Partition}:ec2:${AWS::Region}:${AWS::AccountId}:" + kind + "/*") for kind in ["instance", "volume", "security-group", "network-interface", "key-pair"]], region_cond),
        policy(["ec2:CreateVolume", "ec2:CreateSecurityGroup", "ec2:CreateKeyPair", "ec2:CreateTags", "ec2:AttachVolume", "ec2:AuthorizeSecurityGroupIngress", "ec2:AuthorizeSecurityGroupEgress", "ec2:RevokeSecurityGroupEgress"], condition=region_cond),
        policy(["ec2:TerminateInstances", "ec2:DeleteVolume", "ec2:DeleteSecurityGroup", "ec2:DeleteKeyPair", "ec2:DetachVolume"], condition=managed_cond),
        policy(["ssm:PutParameter", "ssm:DeleteParameter"], sub("arn:${AWS::Partition}:ssm:${AWS::Region}:${AWS::AccountId}:parameter/ec2/keypair/*")),
        policy("iam:PassRole", att("SessionRole"), {"StringEquals": {"iam:PassedToService": "ec2.amazonaws.com"}})])
    r["BackupRole"] = role("backup.amazonaws.com", [], [sub("arn:${AWS::Partition}:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup")])
    r["BackupRole"]["Properties"].pop("Policies")
    r["BackupVault"] = {"Type": "AWS::Backup::BackupVault", "DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain", "Properties": {"BackupVaultName": sub("${AWS::StackName}-data")}}
    r["S3BackupBucket"] = {"Type": "AWS::S3::Bucket", "DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain", "Properties": {
        "PublicAccessBlockConfiguration": {k: True for k in ["BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets"]},
        "BucketEncryption": {"ServerSideEncryptionConfiguration": [{"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]},
        "OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}}}
    r["S3BackupBucketPolicy"] = {"Type": "AWS::S3::BucketPolicy", "Properties": {"Bucket": ref("S3BackupBucket"), "PolicyDocument": {
        "Version": "2012-10-17", "Statement": [{"Effect": "Deny", "Principal": "*", "Action": "s3:*",
            "Resource": [att("S3BackupBucket"), sub("${S3BackupBucket.Arn}/*")], "Condition": {"Bool": {"aws:SecureTransport": "false"}}}]}}}
    env_bucket = sub("arn:${AWS::Partition}:s3:::cloudhub-${AWS::AccountId}-${AWS::Region}-*")
    env_objects = sub("arn:${AWS::Partition}:s3:::cloudhub-${AWS::AccountId}-${AWS::Region}-*/*")
    cf_statements = r["EnvironmentCfRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    cf_statements.extend([
        policy(["s3:CreateBucket", "s3:DeleteBucket", "s3:GetBucketLocation", "s3:GetBucketTagging", "s3:PutBucketTagging",
            "s3:GetBucketVersioning", "s3:PutBucketVersioning", "s3:GetBucketPublicAccessBlock", "s3:PutBucketPublicAccessBlock",
            "s3:GetEncryptionConfiguration", "s3:PutEncryptionConfiguration", "s3:GetBucketOwnershipControls", "s3:PutBucketOwnershipControls",
            "s3:GetBucketPolicy", "s3:PutBucketPolicy", "s3:DeleteBucketPolicy", "s3:ListBucket"], env_bucket),
        policy(["cloudfront:CreateDistribution", "cloudfront:CreateDistributionWithTags", "cloudfront:CreateOriginAccessControl"], "*"),
        policy(["cloudfront:GetDistribution", "cloudfront:GetDistributionConfig", "cloudfront:UpdateDistribution", "cloudfront:DeleteDistribution",
            "cloudfront:TagResource", "cloudfront:UntagResource", "cloudfront:ListTagsForResource"], sub("arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:distribution/*")),
        policy(["cloudfront:GetOriginAccessControl", "cloudfront:GetOriginAccessControlConfig", "cloudfront:UpdateOriginAccessControl", "cloudfront:DeleteOriginAccessControl"], sub("arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:origin-access-control/*"))])
    r["ScheduleGroup"] = {"Type": "AWS::Scheduler::ScheduleGroup", "Properties": {"Name": sub("${AWS::StackName}-sessions")}}
    r["Dlq"] = {"Type": "AWS::SQS::Queue", "Properties": {"MessageRetentionPeriod": 1209600, "SqsManagedSseEnabled": True}}
    expiry_arn = sub("arn:${AWS::Partition}:lambda:${AWS::Region}:${AWS::AccountId}:function:${AWS::StackName}-expiry")
    r["SchedulerRole"] = role("scheduler.amazonaws.com", [policy("lambda:InvokeFunction", expiry_arn), policy("sqs:SendMessage", att("Dlq"))])
    settings = {"vpc_id": "${VpcId}", "public_subnet_id": "${PublicSubnetId}", "availability_zone": "${AvailabilityZone}", "ami_id": "${AmiId}",
        "ssh_cidr": "${SshCidr}",
        "instance_profile": "${SessionProfile}", "cf_role_arn": "${EnvironmentCfRole.Arn}", "schedule_group": "${ScheduleGroup}",
        "expiry_arn": "arn:${AWS::Partition}:lambda:${AWS::Region}:${AWS::AccountId}:function:${AWS::StackName}-expiry",
        "scheduler_role": "${SchedulerRole.Arn}", "dlq_arn": "${Dlq.Arn}", "backup_vault": "${BackupVault}",
        "backup_role": "${BackupRole.Arn}", "s3_backup_bucket": "${S3BackupBucket}", "account_id": "${AWS::AccountId}", "region": "${AWS::Region}"}
    state_arn = sub("arn:${AWS::Partition}:states:${AWS::Region}:${AWS::AccountId}:stateMachine:${AWS::StackName}-lifecycle")
    r['RatesParameter']={'Type':'AWS::SSM::Parameter','Properties':{'Name':sub('/cloudhub/${AWS::StackName}/prices'),'Type':'String','Tier':'Standard','Value':ref('PricesJson')}}
    common = {"TABLE_NAME": ref("Table"), "PRICES_PARAMETER": ref('RatesParameter'), "BUDGET_MILLIUSD": ref("BudgetMilliusd"), "VERIFIED_TEMPLATES": ref("VerifiedTemplates"), "VALIDATION_TEMPLATES": ref("ValidationTemplates"), "USER_LIMIT": ref("UserLimit"), "TOTAL_LIMIT": ref("TotalLimit"), "STATE_MACHINE_ARN": state_arn}
    data_policy = policy(["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:TransactWriteItems", "dynamodb:Query", "dynamodb:Scan"], [att("Table"), sub("${Table.Arn}/index/*")])
    start_policy = policy("states:StartExecution", state_arn)
    worker_policies = [data_policy,
        policy(["cloudformation:CreateStack", "cloudformation:DeleteStack", "cloudformation:DescribeStacks", "cloudformation:ListStackResources"], sub("arn:${AWS::Partition}:cloudformation:${AWS::Region}:${AWS::AccountId}:stack/cloudhub-env-*/*")),
        policy(["ec2:Describe*"], condition=region_cond),
        policy(["ec2:StopInstances", "ec2:StartInstances"], condition=managed_cond),
        policy(["ssm:DescribeInstanceInformation", "ssm:GetCommandInvocation"], condition=region_cond),
        policy("ssm:GetParameter", sub("arn:${AWS::Partition}:ssm:${AWS::Region}:${AWS::AccountId}:parameter/ec2/keypair/*")),
        policy("ssm:SendCommand", sub("arn:${AWS::Partition}:ssm:${AWS::Region}::document/AWS-RunShellScript")),
        policy("ssm:SendCommand", sub("arn:${AWS::Partition}:ec2:${AWS::Region}:${AWS::AccountId}:instance/*"), {"StringEquals": {"ssm:resourceTag/cloudhub:managed": "true"}}),
        policy(["scheduler:CreateSchedule"], sub("arn:${AWS::Partition}:scheduler:${AWS::Region}:${AWS::AccountId}:schedule/${ScheduleGroup}/*")),
        policy("backup:StartBackupJob", att("BackupVault")),
        policy(["backup:DescribeBackupJob", "backup:DeleteRecoveryPoint"], "*", region_cond),
        policy("iam:PassRole", att("EnvironmentCfRole"), {"StringEquals": {"iam:PassedToService": "cloudformation.amazonaws.com"}}),
        policy("iam:PassRole", att("SchedulerRole"), {"StringEquals": {"iam:PassedToService": "scheduler.amazonaws.com"}}),
        policy("iam:PassRole", att("BackupRole"), {"StringEquals": {"iam:PassedToService": "backup.amazonaws.com"}})]
    worker_policies.extend([
        policy(["s3:GetBucketTagging", "s3:ListBucket", "s3:ListBucketVersions", "s3:GetBucketPolicy"], env_bucket),
        policy(["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion"], env_objects),
        policy("s3:ListBucket", att("S3BackupBucket"), {"StringLike": {"s3:prefix": "recovery/rp-*/*"}}),
        policy(["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], sub("${S3BackupBucket.Arn}/recovery/rp-*/*")),
        policy("cloudfront:GetDistribution", sub("arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:distribution/*")),
        policy("cloudfront:GetOriginAccessControl", sub("arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:origin-access-control/*"))])
    from infra_extensions import extend
    extend(params,r,settings,cf_statements,worker_policies)
    for name, suffix, handler, statements in [
        ("ApiFunction", "api", "cloudhub.api.handler", [data_policy, start_policy,
            policy(["s3:GetBucketTagging", "s3:ListBucket"], env_bucket),
            policy('s3:GetObjectVersion',env_objects),
            policy("ec2:DescribeKeyPairs", condition=region_cond),
            policy("ssm:GetParameter", sub("arn:${AWS::Partition}:ssm:${AWS::Region}:${AWS::AccountId}:parameter/ec2/keypair/*"))]),
        ("WorkerFunction", "worker", "cloudhub.worker.handler", worker_policies),
        ("ExpiryFunction", "expiry", "cloudhub.runtime.expiry_handler", [data_policy, start_policy]),
        ("SweepFunction", "sweep", "cloudhub.runtime.sweep_handler", worker_policies + [start_policy, policy("states:DescribeExecution", sub("arn:${AWS::Partition}:states:${AWS::Region}:${AWS::AccountId}:execution:${AWS::StackName}-lifecycle:*"))])]:
        env = dict(common)
        statements=list(statements)+[policy('ssm:GetParameter',sub('arn:${AWS::Partition}:ssm:${AWS::Region}:${AWS::AccountId}:parameter/cloudhub/${AWS::StackName}/prices'))]
        if name in {"WorkerFunction", "SweepFunction", "ApiFunction"}: env["DRIVER_SETTINGS"] = sub(json.dumps(settings,separators=(',',':')))
        r[name] = {"Type": "AWS::Serverless::Function", "Properties": {
            "FunctionName": sub("${AWS::StackName}-" + suffix), "CodeUri": "../backend/", "Handler": handler, "Runtime": "python3.13", "Architectures": ["x86_64"],
            "Timeout": 60 if name != "ApiFunction" else 5, "MemorySize": 256,
            "Environment": {"Variables": env}, "Policies": [{"Version": "2012-10-17", "Statement": statements}]}}
        r[name + "Log"] = {"Type": "AWS::Logs::LogGroup", "Properties": {"LogGroupName": sub("/aws/lambda/${AWS::StackName}-" + suffix), "RetentionInDays": 14}}
    routes = [("GET", "/templates"), ("POST", "/estimates"), ("GET", "/environments"), ("POST", "/environments"),
        ("GET", "/environments/{environment_id}"), ("POST", "/environments/{environment_id}/{action}"),
        ("GET", "/environments/{environment_id}/ssh-key"),
        ("GET", "/environments/{environment_id}/files"), ("POST", "/environments/{environment_id}/files"),
        ("GET", "/operations/{operation_id}"), ("GET", "/recovery-points"), ("GET", "/recovery-points/{recovery_point}"), ("GET", "/admin/attention")]
    r["ApiFunction"]["Properties"]["Events"] = {"Route" + str(i): {"Type": "HttpApi", "Properties": {"ApiId": ref("HttpApi"), "Method": method, "Path": path}} for i, (method, path) in enumerate(routes)}
    r["StateMachine"] = {"Type": "AWS::Serverless::StateMachine", "Properties": {"Name": sub("${AWS::StackName}-lifecycle"), "Type": "STANDARD", "DefinitionUri": "lifecycle.asl.json", "DefinitionSubstitutions": {"WorkerArn": att("WorkerFunction")}, "Policies": [{"LambdaInvokePolicy": {"FunctionName": ref("WorkerFunction")}}]}}
    r["FallbackScan"] = {"Type": "AWS::Scheduler::Schedule", "Properties": {"GroupName": ref("ScheduleGroup"), "ScheduleExpression": "rate(5 minutes)", "FlexibleTimeWindow": {"Mode": "OFF"}, "Target": {"Arn": att("SweepFunction"), "RoleArn": att("SweepSchedulerRole"), "RetryPolicy": {"MaximumRetryAttempts": 2, "MaximumEventAgeInSeconds": 300}, "DeadLetterConfig": {"Arn": att("Dlq")}}}}
    r["SweepSchedulerRole"] = role("scheduler.amazonaws.com", [policy("lambda:InvokeFunction", att("SweepFunction")), policy("sqs:SendMessage", att("Dlq"))])
    r["FailureMetric"] = {"Type": "AWS::Logs::MetricFilter", "Properties": {"LogGroupName": ref("WorkerFunctionLog"), "FilterPattern": '{ $.event = "CloudHubOperationFailed" }', "MetricTransformations": [{"MetricNamespace": "CloudHub", "MetricName": "OperationFailed", "MetricValue": "1"}]}}
    r["AlertTopic"] = {"Type": "AWS::SNS::Topic"}
    r["FailureAlarm"] = {"Type": "AWS::CloudWatch::Alarm", "Properties": {"Namespace": "CloudHub", "MetricName": "OperationFailed", "Statistic": "Sum", "Period": 60, "EvaluationPeriods": 1, "Threshold": 1, "ComparisonOperator": "GreaterThanOrEqualToThreshold", "TreatMissingData": "notBreaching", "AlarmActions": [ref("AlertTopic")]}}
    for name in ["ApiFunction", "WorkerFunction", "SweepFunction", "ExpiryFunction"]:
        r[name + "ErrorAlarm"] = {"Type": "AWS::CloudWatch::Alarm", "Properties": {"Namespace": "AWS/Lambda", "MetricName": "Errors", "Dimensions": [{"Name": "FunctionName", "Value": ref(name)}], "Statistic": "Sum", "Period": 60, "EvaluationPeriods": 1, "Threshold": 1, "ComparisonOperator": "GreaterThanOrEqualToThreshold", "TreatMissingData": "notBreaching", "AlarmActions": [ref("AlertTopic")]}}
    r['DlqAlarm']={'Type':'AWS::CloudWatch::Alarm','Properties':{'Namespace':'AWS/SQS','MetricName':'ApproximateNumberOfMessagesVisible',
        'Dimensions':[{'Name':'QueueName','Value':att('Dlq','QueueName')}],'Statistic':'Maximum','Period':60,'EvaluationPeriods':1,'Threshold':1,
        'ComparisonOperator':'GreaterThanOrEqualToThreshold','TreatMissingData':'notBreaching','AlarmActions':[ref('AlertTopic')]}}
    widgets=[{'type':'metric','x':0,'y':0,'width':12,'height':6,'properties':{'title':'HTTP API latency / errors','region':'${AWS::Region}',
        'metrics':[['AWS/ApiGateway','Latency','ApiId','${HttpApi}',{'stat':'p95'}],['AWS/ApiGateway','5xx','ApiId','${HttpApi}',{'stat':'Sum'}]],'period':60}},
        {'type':'metric','x':12,'y':0,'width':12,'height':6,'properties':{'title':'Lifecycle failures and Lambda errors','region':'${AWS::Region}',
        'metrics':[['CloudHub','OperationFailed'],['AWS/Lambda','Errors','FunctionName','${WorkerFunction}'],['AWS/Lambda','Errors','FunctionName','${SweepFunction}']],
        'stat':'Sum','period':60}}]
    r['OperationsDashboard']={'Type':'AWS::CloudWatch::Dashboard','Properties':{'DashboardName':sub('${AWS::StackName}-operations'),'DashboardBody':sub(json.dumps({'widgets':widgets}))}}
    outputs = {"FrontendUrl": {"Value": sub("https://${FrontendDistribution.DomainName}")}, "FrontendBucket": {"Value": ref("FrontendBucket")}, "DistributionId": {"Value": ref("FrontendDistribution")},
        "ApiUrl": {"Value": sub("https://${HttpApi}.execute-api.${AWS::Region}.amazonaws.com")}, "UserPoolId": {"Value": ref("UserPool")}, "ClientId": {"Value": ref("UserClient")},
        "CognitoDomain": {"Value": sub("https://${UserDomain}.auth.${AWS::Region}.amazoncognito.com")}, "AlertTopic": {"Value": ref("AlertTopic")}, "TableName": {"Value": ref("Table")}, "BackupVault": {"Value": ref("BackupVault")}, "S3BackupBucket": {"Value": ref("S3BackupBucket")}}
    from portal_infra import extend as extend_portal
    conditions=extend_portal(params,r,common,policy,ref,sub,att)
    outputs['Region']={'Value':ref('AWS::Region')}
    template = {"AWSTemplateFormatVersion": "2010-09-09", "Transform": "AWS::Serverless-2016-10-31", "Description": "CloudHub D01-D13 T01-T10 source; AWS validation required", "Parameters": params, "Conditions":conditions,"Resources": r, "Outputs": outputs}
    (ROOT / "infra/control-plane.json").write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Generated infra/control-plane.json")

if __name__ == "__main__": main()
