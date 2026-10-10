"""T09 resources and the fixed Cognito owner read API."""
from pathlib import Path
from .cf_common import allow,att,name,ref,role,sub,tags,template
def data_template(env,settings,recovery=None):
    r={'DataTable':{'Type':'AWS::DynamoDB::Table','Properties':{'TableName':name(env,'-data'),'BillingMode':'PAY_PER_REQUEST',
        'KeySchema':[{'AttributeName':'row_id','KeyType':'HASH'}],'AttributeDefinitions':[{'AttributeName':'row_id','AttributeType':'S'}],
        'SSESpecification':{'SSEEnabled':True},'Tags':tags(env)}}}
    r['ReadRole']=role(env,settings,'lambda.amazonaws.com',[
        allow('dynamodb:Scan',att('DataTable')),allow('dynamodb:GetItem',settings['management_table_arn'],{'ForAllValues:StringEquals':{'dynamodb:LeadingKeys':['E#'+env['environment_id'],'U#'+env['owner_id']]}}),
        allow(['logs:CreateLogStream','logs:PutLogEvents'],sub('arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:/aws/lambda/'+name(env,'-data')+':*'))])
    r['ReadFunction']={'Type':'AWS::Lambda::Function','Properties':{'FunctionName':name(env,'-data'),'Runtime':'python3.13','Handler':'index.handler',
        'Role':att('ReadRole'),'MemorySize':128,'Timeout':5,
        'Code':{'ZipFile':Path(__file__).with_name('data_api_app.py').read_text(encoding='utf-8')},
        'Environment':{'Variables':{'OWNER':env['owner_id'],'ENVIRONMENT':env['environment_id'],'MANAGEMENT':settings['management_table'],
            'DATA_TABLE':ref('DataTable')}},'Tags':tags(env)}}
    r['LogGroup']={'Type':'AWS::Logs::LogGroup','Properties':{'LogGroupName':'/aws/lambda/'+name(env,'-data'),'RetentionInDays':7}}
    r['DataApi']={'Type':'AWS::ApiGatewayV2::Api','Properties':{'Name':name(env,'-api'),'ProtocolType':'HTTP',
        'CorsConfiguration':{'AllowOrigins':[settings['frontend_origin']],'AllowHeaders':['Authorization'],'AllowMethods':['GET','OPTIONS'],'MaxAge':300},
        'Tags':{t['Key']:t['Value'] for t in tags(env)}}}
    r['Authorizer']={'Type':'AWS::ApiGatewayV2::Authorizer','Properties':{'ApiId':ref('DataApi'),'Name':'owner','AuthorizerType':'JWT',
        'IdentitySource':['$request.header.Authorization'],'JwtConfiguration':{'Audience':[settings['cognito_client_id']],'Issuer':settings['cognito_issuer']}}}
    r['Integration']={'Type':'AWS::ApiGatewayV2::Integration','Properties':{'ApiId':ref('DataApi'),'IntegrationType':'AWS_PROXY',
        'IntegrationUri':att('ReadFunction'),'PayloadFormatVersion':'2.0'}}
    r['Route']={'Type':'AWS::ApiGatewayV2::Route','Properties':{'ApiId':ref('DataApi'),'RouteKey':'GET /records','Target':sub('integrations/${Integration}'),
        'AuthorizationType':'JWT','AuthorizerId':ref('Authorizer'),'AuthorizationScopes':['cloudhub/use']}}
    r['Stage']={'Type':'AWS::ApiGatewayV2::Stage','Properties':{'ApiId':ref('DataApi'),'StageName':'$default','AutoDeploy':True,
        'DefaultRouteSettings':{'ThrottlingBurstLimit':10,'ThrottlingRateLimit':5}}}
    r['InvokePermission']={'Type':'AWS::Lambda::Permission','Properties':{'FunctionName':ref('ReadFunction'),'Action':'lambda:InvokeFunction',
        'Principal':'apigateway.amazonaws.com','SourceArn':sub('arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:${DataApi}/*/GET/records')}}
    return template(r,{'TableName':ref('DataTable'),'FunctionName':ref('ReadFunction'),'ApiId':ref('DataApi'),'ApiUrl':sub('${DataApi.ApiEndpoint}/records')})
