"""Product endpoints and honest states for source version 0.4."""
def extend(paths,schemas,operation,param,ref):
    integer={'type':'integer','minimum':0}
    def obj(properties):
        return {'type':'object','additionalProperties':False,'required':list(properties),'properties':properties}
    schemas.update({
        'Limits':obj({**{k:integer for k in ['active','max_active','remaining_environments','user_reserved_milliusd','user_budget_milliusd','remaining_milliusd','global_remaining_environments','global_remaining_milliusd']},'budget_method':{'type':'string'}}),
        'QuotaInput':obj({'max_active':integer,'budget_milliusd':integer}),
        'InviteInput':obj({'email':{'type':'string','format':'email','maxLength':254},'send_invitation':{'type':'boolean','enum':[True]}}),
        'EnabledInput':obj({'enabled':{'type':'boolean'}}),
        'RetireInput':obj({'reason':{'type':'string','minLength':10,'maxLength':500}}),
        'SnsTestInput':obj({'confirm_send':{'type':'boolean','enum':[True]}}),
        'Account':{'type':'object','required':['username','owner_id','email','enabled','status'],'properties':{
            'username':{'type':'string'},'owner_id':{'type':'string','nullable':True},'email':{'type':'string'},'email_verified':{'type':'boolean'},
            'enabled':{'type':'boolean','description':'Effective CloudHub access; local disable fails closed.'},'cognito_enabled':{'type':'boolean'},'status':{'type':'string'},'limits':ref('Limits'),
            'invitation_status':{'type':'string','enum':['CREATED','ALREADY_EXISTS_NO_RESEND']}}},
        'AccountList':{'type':'object','required':['items'],'properties':{'items':{'type':'array','items':ref('Account')},'next_cursor':{'type':'string','nullable':True}}},
        'CostReport':{'type':'object','required':['status','actual_milliusd'],'properties':{
            'status':{'type':'string','enum':['NOT_REQUESTED','NOT_CONFIGURED','TAGS_INACTIVE','PENDING','PROVISIONAL','UNAVAILABLE']},
            'actual_milliusd':{'type':'integer','nullable':True,'description':'Gross tagged UnblendedCost; missing data is null, never assumed zero.'},
            'difference_milliusd':{'type':'integer','nullable':True},'final':{'type':'boolean','enum':[False]},'checked_at':{'type':'integer'},'message':{'type':'string'}}},
        'NotificationList':{'type':'object','required':['items','warning_minutes'],'properties':{
            'warning_minutes':{'type':'integer','enum':[15]},'items':{'type':'array','items':{'type':'object','properties':{
                **{k:{'type':'string'} for k in ['notification_id','environment_id','message']},'type':{'type':'string','enum':['EXPIRY_SOON']},'deadline':{'type':'integer'},'remaining_seconds':integer}}}}},
        'SnsStatus':{'type':'object','required':['status','email'],'properties':{'status':{'type':'string','enum':['NOT_CONFIGURED','NOT_SUBSCRIBED','PENDING_CONFIRMATION','CONFIRMED']},'email':{'type':'string'},'delivery_verified':{'type':'boolean','enum':[False]},'note':{'type':'string'}}},
        'Download':{'type':'object','required':['url','expires_at','label'],'properties':{'url':{'type':'string','format':'uri'},'expires_at':{'type':'integer'},'label':{'type':'string'},'contains_snapshot_data':{'type':'boolean'}}},
        'OperationList':{'type':'object','required':['items'],'properties':{'items':{'type':'array','items':ref('Operation')}}}
    })
    endpoints=[
        ('/limits','get','getLimits',None,[],'200','Limits',False),
        ('/notifications','get','expiryNotifications',None,[],'200','NotificationList',False),
        ('/operations','get','operationHistory',None,[],'200','OperationList',False),
        ('/operations/{operation_id}/retry','post','retryFailedOperation','Empty',[param('operation_id')],'202','Operation',True),
        ('/operations/{operation_id}/cancel','post','closeFailedOperation','Empty',[param('operation_id')],'200','Operation',True),
        ('/operations/{operation_id}/download','get','downloadExport',None,[param('operation_id')],'200','Download',False),
        ('/recovery-points/{recovery_point}/delete','post','deleteBackup','Empty',[param('recovery_point')],'202','Operation',True),
        ('/recovery-points/{recovery_point}/export','post','exportBackup','Empty',[param('recovery_point')],'202','Operation',True),
        ('/environments/{environment_id}/cost','get','getActualCost',None,[param('environment_id')],'200','CostReport',False),
        ('/environments/{environment_id}/cost','post','reconcileActualCost','Empty',[param('environment_id')],'202','Operation',True),
        ('/admin/environments','get','adminAllEnvironments',None,[],'200','EnvironmentList',False),
        ('/admin/operations/{operation_id}','get','adminGetOperation',None,[param('operation_id')],'200','Operation',False),
        ('/admin/environments/{environment_id}/retire','post','adminRetire','RetireInput',[param('environment_id')],'202','Operation',True),
        ('/admin/users','get','adminListUsers',None,[{'name':'cursor','in':'query','schema':{'type':'string','maxLength':4096}}],'200','AccountList',False),
        ('/admin/users','post','adminInviteUser','InviteInput',[],'201','Account',True),
        ('/admin/users/{owner_id}/limits','post','adminSetLimits','QuotaInput',[param('owner_id')],'200','Limits',False),
        ('/admin/users/{owner_id}/enabled','post','adminSetEnabled','EnabledInput',[param('owner_id')],'200','Account',False),
        ('/admin/notifications','get','adminSnsStatus',None,[],'200','SnsStatus',False),
        ('/admin/notifications/test','post','adminTestSns','SnsTestInput',[],'200','Object',False),
    ]
    for path,method,opid,body,params,status,result,idem in endpoints:
        paths.setdefault(path,{})[method]=operation(opid,method,body,params,status,result,idem)
    for path,methods in paths.items():
        for op in methods.values():
            op['responses']['403']={'description':'Account disabled or current admin role required','content':{'application/json':{'schema':ref('Error')}}}
            if path.startswith('/admin/'):
                op['description']='Admin JWT and enabled Cognito account with current admin group membership required.'
    paths['/admin/users']['post']['responses']['200']={'description':'Existing account; invitation is never resent automatically','content':{'application/json':{'schema':ref('Account')}}}
    paths['/operations/{operation_id}/cancel']['post']['description']='Only FAILED operations. Closes the error; preserves resources and quota. Does not stop active workflows.'
    paths['/operations/{operation_id}/retry']['post']['description']='Only FAILED and terminal workflow. Up to three manual retries; reuses original environment. Provisioning CREATE/WAIT_CREATE cannot be retried.'
    paths['/recovery-points/{recovery_point}/export']['post']['description']='Portable data for T02/T06/T07/T09; S3 plus native snapshot references for T10; snapshot references only for T01/T03/T04/T05/T08.'
    paths['/environments/{environment_id}/cost']['post']['description']='Opt-in Cost Explorer and active allocation tag required. At most one request per environment per 24 hours; provisional daily gross cost excluding shared infrastructure and credits.'
    props=schemas['Operation']['properties']
    props['action']['enum']+=['delete_backup','export_backup','cost']
    props['status']['enum'].append('CANCELLED')
    props.update({k:integer for k in ['generation','manual_retries']})
    props.update({'failed_phase':{'type':'string'},'admin_actor':{'type':'string'},'admin_reason':{'type':'string'},'cancel_note':{'type':'string'}})
    schemas['RecoveryPoint']['properties']['status']['enum']+=['DELETING','DELETE_FAILED','DELETED']
    schemas['RecoveryPoint']['properties']['in_use']={'type':'boolean','description':'Restore, export or delete lock is held.'}
    schemas['RecoveryPoint']['properties']['retirement_protected']={'type':'boolean','description':'Final backup deletion and retention expiry are blocked until source cleanup is confirmed.'}
    paths['/recovery-points/{recovery_point}/delete']['post']['description']='Returns 409 while this recovery point protects a source whose cleanup has not been confirmed.'
    schemas['Environment']['properties'].update({'retired_at':{'type':'integer'},'cost_report':ref('CostReport')})
