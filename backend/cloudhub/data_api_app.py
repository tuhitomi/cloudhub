"""T09 deployed read API; JWT is verified by API Gateway, ownership checked here."""
import json,os,time
import boto3
from botocore.config import Config
ddb=boto3.client('dynamodb',config=Config(connect_timeout=1,read_timeout=2,retries={'total_max_attempts':1}))
def handler(event,context=None):
    claims=event.get('requestContext',{}).get('authorizer',{}).get('jwt',{}).get('claims',{})
    if claims.get('sub')!=os.environ['OWNER'] or claims.get('token_use')!='access':return reply(404,{'error':'NOT_FOUND'})
    account=ddb.get_item(TableName=os.environ['MANAGEMENT'],Key={'pk':{'S':'U#'+os.environ['OWNER']}},ConsistentRead=True).get('Item',{})
    if account.get('enabled',{}).get('BOOL') is False:return reply(403,{'error':'ACCOUNT_DISABLED'})
    meta=ddb.get_item(TableName=os.environ['MANAGEMENT'],Key={'pk':{'S':'E#'+os.environ['ENVIRONMENT']}},ConsistentRead=True).get('Item',{})
    if meta.get('status',{}).get('S')!='READY' or float(meta.get('deadline',{}).get('N','0'))<=time.time():return reply(409,{'error':'SESSION_UNAVAILABLE'})
    rows=[];params={'TableName':os.environ['DATA_TABLE'],'ConsistentRead':True}
    while True:
        response=ddb.scan(**params)
        rows.extend({'record_id':r['row_id']['S'][2:],'text':r['text']['S']} for r in response['Items'] if r['row_id']['S'].startswith('R#'))
        if len(rows)>32:return reply(409,{'error':'DATA_QUOTA'})
        if not response.get('LastEvaluatedKey'):break
        params['ExclusiveStartKey']=response['LastEvaluatedKey']
    return reply(200,{'items':sorted(rows,key=lambda v:v['record_id'])})
def reply(status,body):return {'statusCode':status,'headers':{'content-type':'application/json','cache-control':'no-store'},'body':json.dumps(body,ensure_ascii=False)}
