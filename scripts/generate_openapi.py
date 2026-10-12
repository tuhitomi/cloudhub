import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def schema_ref(name):return {'$ref':'#/components/schemas/'+name}
def response(description,name='Object'):
    return {'description':description,'content':{'application/json':{'schema':schema_ref(name)}}}
def param(name):return {'name':name,'in':'path','required':True,'schema':{'type':'string','pattern':'^[a-z0-9-]+$'}}
def operation(opid,method='get',body=None,params=None,status='200',result='Object',idempotent=False):
    op={'operationId':opid,'security':[{'Cognito':['cloudhub/use']},{'CognitoAccessToken':[]}],'responses':{status:response('Accepted' if status=='202' else 'OK',result),
        '400':response('Invalid input','Error'),'401':response('Authentication required','Error'),'404':response('Owned entity not found','Error'),'409':response('Conflict, quota or verification gate','Error'),'500':response('Internal error','Error')}}
    op['parameters']=params or []
    if idempotent:op['parameters'].append({'name':'Idempotency-Key','in':'header','required':True,'schema':{'type':'string','pattern':'^[A-Za-z0-9_-]{8,100}$'}})
    if body:op['requestBody']={'required':True,'content':{'application/json':{'schema':schema_ref(body)}}}
    return op
paths={}
for path,method,opid,body,params,status,result,idem in [
    ('/templates','get','listTemplates',None,[],'200','Object',False),
    ('/estimates','post','estimate','EstimateInput',[],'200','Object',False),
    ('/environments','get','listEnvironments',None,[],'200','Object',False),
    ('/environments','post','createOrRestore','CreateInput',[],'202','Operation',True),
    ('/environments/{environment_id}','get','getEnvironment',None,[param('environment_id')],'200','Object',False),
    ('/environments/{environment_id}/ssh-key','get','getOwnedSshKey',None,[param('environment_id')],'200','Object',False),
    ('/environments/{environment_id}/files','get','listOwnedFiles',None,[param('environment_id')],'200','FileList',False),
    ('/environments/{environment_id}/files','post','writeOwnedFile','FileInput',[param('environment_id')],'202','Operation',True),
    ('/operations/{operation_id}','get','getOperation',None,[param('operation_id')],'200','Operation',False),
    ('/recovery-points','get','listRecoveryPoints',None,[],'200','Object',False),
    ('/recovery-points/{recovery_point}','get','getRecoveryPoint',None,[param('recovery_point')],'200','Object',False),
    ('/admin/attention','get','adminAttention',None,[],'200','Object',False)]:
    paths.setdefault(path,{})[method]=operation(opid,method,body,params,status,result,idem)
for action in ['backup','destroy','audit','extend']:
    paths['/environments/{environment_id}/'+action]={'post':operation(action,'post','ExtendInput' if action=='extend' else 'Empty',[param('environment_id')],'200' if action=='extend' else '202','Object' if action=='extend' else 'Operation',True)}
schemas={'Object':{'type':'object','additionalProperties':True},'Empty':{'type':'object','additionalProperties':False},
    'Error':{'type':'object','required':['error'],'properties':{'error':{'type':'object','required':['code','message','request_id'],'properties':{k:{'type':'string'} for k in ['code','message','request_id']}}}},
    'EstimateInput':{'type':'object','additionalProperties':False,'required':['template_id','duration_hours'],'properties':{'template_id':{'type':'string','enum':['T01','T02','T03','T04','T05','T06','T07','T08','T09','T10']},'duration_hours':{'type':'integer','minimum':1,'maximum':8}}},
    'CreateInput':{'type':'object','additionalProperties':False,'required':['template_id','duration_hours'],'properties':{'template_id':{'type':'string','enum':['T01','T02','T03','T04','T05','T06','T07','T08','T09','T10']},'duration_hours':{'type':'integer','minimum':1,'maximum':8},'configuration':{'type':'object','description':'Omit or submit the exact catalog configuration. Backend rejects mixed/partial configurations.','additionalProperties':False,'properties':{'instance_type':{'type':'string','enum':['t3.micro']},'root_gib':{'type':'integer','enum':[16]},'data_gib':{'type':'integer','enum':[8]},'max_files':{'type':'integer','enum':[32]},'max_file_bytes':{'type':'integer','enum':[8192]},'max_total_bytes':{'type':'integer','enum':[262144]},'max_writes':{'type':'integer','enum':[128]}},'oneOf':[{'maxProperties':0},{'required':['instance_type','root_gib','data_gib'],'maxProperties':3},{'required':['max_files','max_file_bytes','max_total_bytes','max_writes'],'maxProperties':4}]},'recovery_point':{'type':'string','pattern':'^rp-[a-f0-9]{24}$'},'validation_run':{'type':'boolean','default':False}}},
    'FileInput':{'type':'object','additionalProperties':False,'required':['key','content_base64','content_type'],'properties':{'key':{'type':'string','maxLength':128,'pattern':'^[A-Za-z0-9][A-Za-z0-9._/-]*$','description':'No empty, dot, parent or .cloudhub segments.'},'content_base64':{'type':'string','format':'byte','maxLength':10924,'description':'Decoded bytes <=8192'},'content_type':{'type':'string','enum':['text/html','text/css','text/plain','application/javascript','application/json','image/png','image/jpeg','image/svg+xml']}}},
    'FileList':{'type':'object','required':['items','limits','writes_used'],'properties':{'items':{'type':'array','items':{'type':'object','required':['key','size'],'properties':{'key':{'type':'string'},'size':{'type':'integer'}}}},'limits':schema_ref('Object'),'writes_used':{'type':'integer'}}},
    'ExtendInput':{'type':'object','additionalProperties':False,'required':['hours'],'properties':{'hours':{'type':'integer','minimum':1,'maximum':4}}},
    'Operation':{'type':'object','required':['operation_id','environment_id','job_id','action','status','phase'],'properties':{'operation_id':{'type':'string'},'environment_id':{'type':'string'},'job_id':{'type':'string'},'action':{'type':'string','enum':['create','restore','backup','destroy','audit','write_file','put_record','list_records','load_dataset','query_demo']},'status':{'type':'string','enum':['QUEUED','RUNNING','SUCCEEDED','FAILED']},'phase':{'type':'string'},'error':{'type':'string'}}}}
epoch={'type':'integer','format':'int64','description':'UTC epoch seconds'}
schemas.update({
    'Resource':{'type':'object','required':['id','type'],'properties':{'id':{'type':'string'},'type':{'type':'string'},'logical_id':{'type':'string'},'retain_until':epoch}},
    'CleanupReport':{'type':'object','required':['deleted','retained','residual','unknown','billable','checked_at'],'properties':{
        **{k:{'type':'array','items':schema_ref('Resource')} for k in ['deleted','retained','residual','billable']},
        'unknown':{'type':'array','items':{'type':'object','additionalProperties':True}},'coverage':{'type':'string'},'checked_at':epoch}},
    'Environment':{'type':'object','required':['environment_id','owner_id','template_id','status','duration_hours','created_at','deadline'],'properties':{
        'environment_id':{'type':'string','pattern':'^env-[a-f0-9]{24}$'},'owner_id':{'type':'string','readOnly':True},'template_id':{'type':'string'},
        'status':{'type':'string','enum':['PROVISIONING','READY','BACKING_UP','RETIRING','DELETED','RESIDUAL','ERROR_RETAINED','CREATE_FAILED']},
        'duration_hours':{'type':'integer','minimum':1,'maximum':8},'created_at':epoch,'ready_at':epoch,'deadline':{**epoch,'nullable':True},
        'expiry_triggered_at':epoch,'lock':{'type':'string','nullable':True},'access_address':{'type':'string'},'public_ip':{'type':'string'},
        'estimate':schema_ref('Estimate'),'resources':{'type':'array','items':schema_ref('Resource')},'needs_admin':{'type':'boolean'},'cleanup_report':schema_ref('CleanupReport')}},
    'RecoveryPoint':{'type':'object','required':['recovery_point','environment_id','owner_id','template_id','status','source','scope','created_at','retain_until','restore_verified'],'properties':{
        'recovery_point':{'type':'string','pattern':'^rp-[a-f0-9]{24}$'},'environment_id':{'type':'string'},'owner_id':{'type':'string','readOnly':True},'template_id':{'type':'string'},
        'status':{'type':'string','enum':['PENDING','AVAILABLE','FAILED','EXPIRING','EXPIRED']},'source':{'type':'string','nullable':True},'scope':{'type':'string'},
        'created_at':epoch,'completed_at':epoch,'retain_until':epoch,'restore_verified':{'type':'boolean'},'manifest_sha256':{'type':'string'},'snapshot_id':{'type':'string'},'backup_job_id':{'type':'string'}}},
    'Estimate':{'type':'object','required':['template_id','currency','approved','assumptions','total_milliusd','unknown','cost_data_delay'],'properties':{
        'template_id':{'type':'string'},'currency':{'type':'string','enum':['USD']},'approved':{'type':'boolean'},'total_milliusd':{'type':'integer','nullable':True},
        'region':{'type':'string'},'price_updated_at':{'type':'string'},'units':{'type':'object','additionalProperties':{'oneOf':[{'type':'number'},{'type':'string'}]}},
        'assumptions':schema_ref('Object'),'lines':schema_ref('Object'),'unknown':{'type':'array','items':{'type':'string'}},'cost_data_delay':{'type':'string'}}},
    'SshKey':{'type':'object','description':'Sensitive: owner-only, no-store, never log or persist in browser storage. Download to local OpenSSH client.',
        'required':['environment_id','filename','private_key','public_ip','username','port','command'],'properties':{**{k:{'type':'string'} for k in ['environment_id','filename','private_key','public_ip','username','command']},'port':{'type':'integer','enum':[22]}}}
})
for name,entity in [('EnvironmentList','Environment'),('RecoveryList','RecoveryPoint')]:
    schemas[name]={'type':'object','required':['items'],'properties':{'items':{'type':'array','items':schema_ref(entity)}}}
for path,entity in [('/environments','EnvironmentList'),('/environments/{environment_id}','Environment'),('/recovery-points','RecoveryList'),('/recovery-points/{recovery_point}','RecoveryPoint'),('/environments/{environment_id}/ssh-key','SshKey')]:
    paths[path]['get']['responses']['200']=response('OK',entity)
paths['/estimates']['post']['responses']['200']=response('OK','Estimate')
paths['/admin/attention']['get']['responses']['403']=response('Admin required','Error')
paths['/environments/{environment_id}/files']['post']['responses']['413']=response('File/request too large','Error')
import sys
sys.path.insert(0,str(ROOT/'backend'))
from cloudhub.catalog import IMPLEMENTED,configuration
choices=[{'type':'object','maxProperties':0}]
for code in sorted(IMPLEMENTED):
    cfg=configuration(code);properties={}
    for key,value in cfg.items():
        typ='boolean' if type(value) is bool else 'integer' if type(value) is int else 'string'
        properties[key]={'type':typ,'enum':[value]}
    choices.append({'type':'object','additionalProperties':False,'required':list(cfg),'properties':properties})
# Reused configurations (T01/T05, T02/T07) must not make oneOf ambiguous.
dedup={json.dumps(v,sort_keys=True):v for v in choices}
schemas['CreateInput']['properties']['configuration']={'description':'Omit or submit the exact configuration returned by the catalog.','oneOf':list(dedup.values())}
schemas['RecordInput']={'type':'object','additionalProperties':False,'required':['record_id','text'],'properties':{
    'record_id':{'type':'string','pattern':'^[A-Za-z0-9_-]{1,64}$'},'text':{'type':'string','maxLength':256,'description':'UTF-8 bytes <=256. Newline/tab allowed; other control characters rejected.'}}}
schemas['ShareInput']={'type':'object','additionalProperties':False,'required':['key'],'properties':{'key':schemas['FileInput']['properties']['key']}}
for action in ['put_record','list_records','load_dataset','query_demo']:
    paths['/environments/{environment_id}/'+action]={'post':operation(action,'post','RecordInput' if action in {'put_record','load_dataset'} else 'Empty',[param('environment_id')],'202','Operation',True)}
paths['/environments/{environment_id}/share']={'post':operation('shareFile','post','ShareInput',[param('environment_id')],'200','Object')}
schemas['Operation']['properties']['result']=schema_ref('Object')
from portal_openapi import extend
extend(paths,schemas,operation,param,schema_ref)

doc={'openapi':'3.0.3','info':{'title':'CloudHub','version':'0.3.0','description':'T01–T10 source; AWS validation gate required. All IDs are server-generated. Epoch timestamps in UTC seconds.'},'servers':[{'url':'https://{api_id}.execute-api.{region}.amazonaws.com','variables':{'api_id':{'default':'REQUIRED'},'region':{'default':'REQUIRED'}}}],'paths':paths,'components':{'securitySchemes':{'Cognito':{'type':'oauth2','flows':{'authorizationCode':{'authorizationUrl':'https://REQUIRED.auth.REQUIRED.amazoncognito.com/oauth2/authorize','tokenUrl':'https://REQUIRED.auth.REQUIRED.amazoncognito.com/oauth2/token','scopes':{'cloudhub/use':'Use CloudHub'}}}}},'schemas':schemas}}
doc['components']['securitySchemes']['CognitoAccessToken']={'type':'http','scheme':'bearer','bearerFormat':'JWT','description':'Cognito access token from the same configured user pool and app client, with aws.cognito.signin.user.admin scope. API Gateway verifies signature, issuer, audience and scopes; backend checks access token type, enabled account, ownership and current admin membership.'}
doc['info']['version']='0.4.2'
doc['info']['description']+=' Password reset/change and email verification use Cognito directly; passwords never enter this API.'
(ROOT/'docs/openapi.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding='utf-8')
print('Generated docs/openapi.json')
