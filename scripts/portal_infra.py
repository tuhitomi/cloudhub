"""Add product API, scoped identity administration and optional email/cost wiring."""
def extend(params, resources, common, policy, ref, sub, att):
    params['AlertEmail']={'Type':'String','Default':'','AllowedPattern':'^$|[^\\s@]+@[^\\s@]+\\.[^\\s@]+','Description':'Approved operator email. Recipient must confirm SNS subscription.'}
    params['ActualCostsEnabled']={'Type':'String','Default':'false','AllowedValues':['true','false'],'Description':'Enable manual Cost Explorer queries after operator reviews API charges and activates cloudhub:environment cost allocation tag.'}
    common.update(USER_POOL_ID=ref('UserPool'),ALERT_TOPIC_ARN=ref('AlertTopic'),ALERT_EMAIL=ref('AlertEmail'),ACTUAL_COSTS_ENABLED=ref('ActualCostsEnabled'))
    for name in ['ApiFunction','WorkerFunction','ExpiryFunction','SweepFunction']:
        resources[name]['Properties']['Environment']['Variables'].update(common)
    pool=sub('arn:${AWS::Partition}:cognito-idp:${AWS::Region}:${AWS::AccountId}:userpool/${UserPool}')
    resources['ApiFunction']['Properties']['Policies'][0]['Statement'].extend([
        policy(['cognito-idp:AdminGetUser','cognito-idp:AdminListGroupsForUser','cognito-idp:ListUsers','cognito-idp:AdminCreateUser','cognito-idp:AdminDisableUser','cognito-idp:AdminEnableUser'],pool),
        policy(['sns:ListSubscriptionsByTopic','sns:Publish'],ref('AlertTopic'))])
    resources['ApiFunction']['Properties']['Policies'][0]['Statement'].append(
        policy('states:DescribeExecution',sub('arn:${AWS::Partition}:states:${AWS::Region}:${AWS::AccountId}:execution:${AWS::StackName}-lifecycle:*')))
    resources['WorkerFunction']['Properties']['Policies'][0]['Statement'].append(
        {'Fn::If':['CostsEnabled',policy(['ce:GetCostAndUsage','ce:ListCostAllocationTags'],'*'),ref('AWS::NoValue')]})
    resources['ApiFunction']['Properties']['Policies'][0]['Statement'].append(policy(['s3:GetObject'],sub('${S3BackupBucket.Arn}/recovery/rp-*/exports/*')))
    resources['EmailSubscription']={'Type':'AWS::SNS::Subscription','Condition':'HasAlertEmail',
        'Properties':{'TopicArn':ref('AlertTopic'),'Protocol':'email','Endpoint':ref('AlertEmail')}}
    userpool=resources['UserPool']['Properties']
    userpool['AccountRecoverySetting']={'RecoveryMechanisms':[{'Name':'verified_email','Priority':1}]}
    userpool['AdminCreateUserConfig']={'AllowAdminCreateUserOnly':False}
    userpool['UserAttributeUpdateSettings']={'AttributesRequireVerificationBeforeUpdate':['email']}
    userpool['VerificationMessageTemplate']={'DefaultEmailOption':'CONFIRM_WITH_CODE','EmailSubject':'Mã xác thực tài khoản CloudHub','EmailMessage':'Mã xác thực tài khoản CloudHub của bạn là {####}.'}
    client=resources['UserClient']['Properties']
    client['ReadAttributes']=['sub','email','email_verified']
    client['WriteAttributes']=['email']
    client['ExplicitAuthFlows']=['ALLOW_USER_PASSWORD_AUTH','ALLOW_USER_SRP_AUTH','ALLOW_REFRESH_TOKEN_AUTH']
    client['AllowedOAuthScopes'].append('aws.cognito.signin.user.admin')
    resources['HttpApi']['Properties']['Auth']['Authorizers']['CognitoJwt']['AuthorizationScopes']=['cloudhub/use','aws.cognito.signin.user.admin']
    client['RefreshTokenValidity']=1;client['TokenValidityUnits']['RefreshToken']='days'
    events=resources['ApiFunction']['Properties']['Events']
    routes=[('GET','/limits'),('GET','/notifications'),('GET','/operations'),
        ('POST','/operations/{operation_id}/{control}'),('GET','/operations/{operation_id}/download'),
        ('POST','/recovery-points/{recovery_point}/{control}'),
        ('GET','/environments/{environment_id}/cost'),('POST','/environments/{environment_id}/cost'),
        ('GET','/admin/environments'),('GET','/admin/operations/{operation_id}'),
        ('GET','/admin/users'),('POST','/admin/users'),('POST','/admin/users/{owner_id}/{control}'),
        ('POST','/admin/environments/{environment_id}/retire'),('GET','/admin/notifications'),('POST','/admin/notifications/test'),
        ('GET','/account'),('GET','/admin/signup-defaults'),('POST','/admin/signup-defaults'),
        ('GET','/environments/{environment_id}/renewals'),('POST','/environments/{environment_id}/management-estimate'),
        ('POST','/environments/{environment_id}/renew'),('POST','/environments/{environment_id}/change_policy'),
        ('POST','/environments/{environment_id}/resume_paused'),('POST','/environments/{environment_id}/end')]
    for index,(method,path) in enumerate(routes):
        events['PortalRoute'+str(index)]={'Type':'HttpApi','Properties':{'ApiId':ref('HttpApi'),'Method':method,'Path':path}}
    return {'HasAlertEmail':{'Fn::Not':[{'Fn::Equals':[ref('AlertEmail'),'']}]},'CostsEnabled':{'Fn::Equals':[ref('ActualCostsEnabled'),'true']}}
