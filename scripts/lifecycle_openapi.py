"""Operating-mode API and migration contract, separate from the forecast horizon."""
def extend(paths,schemas,operation,param,ref):
    minutes={'type':'integer','minimum':5,'maximum':480}
    expected={'type':'integer','minimum':0,'description':'Optional UI revision fence; mismatch returns 409.'}
    def obj(props,required=None):return {'type':'object','additionalProperties':False,'properties':props,'required':list(props) if required is None else required}
    policy=obj({'mode':{'type':'string','enum':['AUTO_OFF','AUTO_RENEW']},'period_minutes':minutes,
        'ending_policy':{'type':'string','enum':['PAUSE','BACKUP_RETIRE']},
        'budget_limit_milliusd':{'type':'integer','nullable':True,'minimum':1,'description':'Required positive cumulative lifetime estimate cap for AUTO_RENEW; null for AUTO_OFF.'},
        'confirmed':{'type':'boolean','enum':[True]}})
    policy['oneOf']=[{'properties':{'mode':{'enum':['AUTO_RENEW']},'budget_limit_milliusd':{'type':'integer','minimum':1}}},
        {'properties':{'mode':{'enum':['AUTO_OFF']},'budget_limit_milliusd':{'enum':[None]}}}]
    schemas['OperatingPolicy']=policy
    schemas['OperatingPolicyRead']={**policy,'additionalProperties':True}
    schemas['RenewInput']=obj({'minutes':minutes,'confirmed':{'type':'boolean','enum':[True]},'expected_revision':expected},['minutes','confirmed'])
    schemas['LifecycleConsent']=obj({'confirmed':{'type':'boolean','enum':[True]},'expected_revision':expected},['confirmed'])
    schemas['PolicyInput']=obj({'operating_policy':ref('OperatingPolicy'),'expected_revision':expected},['operating_policy'])
    schemas['ManagementEstimateInput']=obj({'action':{'type':'string','enum':['renew','resume_paused','change_policy','end','destroy','backup']},
        'minutes':minutes,'operating_policy':ref('OperatingPolicy')},['action'])
    for name in ['EstimateInput','CreateInput']:
        schema=schemas[name];schema['required']=['template_id']
        schema['properties'].update({'estimate_hours':{'type':'integer','minimum':1,'maximum':8,'description':'Forecast horizon only; never sets the operating deadline.'},'operating_policy':ref('OperatingPolicy')})
        schema['oneOf']=[{'required':['duration_hours'],'not':{'anyOf':[{'required':['estimate_hours']},{'required':['operating_policy']}]}},
            {'required':['estimate_hours','operating_policy'],'not':{'required':['duration_hours']}}]
        schema['properties']['duration_hours']['description']='Legacy input: explicit automatic backup-retirement after this period; never enables auto renewal.'
    for action,body,status in [('renew','RenewInput','202'),('change_policy','PolicyInput','202'),('resume_paused','LifecycleConsent','202'),('end','LifecycleConsent','202'),('management-estimate','ManagementEstimateInput','200')]:
        paths['/environments/{environment_id}/'+action]={'post':operation(action.replace('-','_'),'post',body,[param('environment_id')],status,'Operation' if status=='202' else 'Object',status=='202')}
    paths['/environments/{environment_id}/renewals']={'get':operation('renewalHistory','get',None,[param('environment_id')],'200','Object')}
    paths['/environments/{environment_id}/extend']['post']=operation('legacyExtend','post','ExtendInput',[param('environment_id')],'202','Operation',True)
    paths['/environments/{environment_id}/extend']['post']['description']='Delegates to shared renewable lifecycle, preserves infrastructure/data and returns a durable operation for schedule sync. No cumulative hours bypass: charges the environment/user/global estimate ledgers.'
    paths['/environments/{environment_id}/change_policy']['post']['description']='Explicit consent, fresh ownership/revision and environment lock. Current deadline is unchanged; budget ledger never resets. Additional terminal storage allowance may be charged.'
    paths['/environments/{environment_id}/renew']['post']['description']='Atomic deadline/revision/estimate update; no processor create/stop/delete. Schedule sync failure keeps the new deadline with a durable retryable operation.'
    paths['/environments/{environment_id}/end']['post']['description']='Applies confirmed PAUSE (T01/T05 only) or BACKUP_RETIRE. Mandatory backup failure retains source, bounded automatic retry and administrative alert.'
    op=schemas['Operation']['properties'];op['action']['enum']+=['renew','change_policy','pause','resume_paused']
    op['phase']['description']='Durable processor phase; lifecycle adds SYNC_SCHEDULE, PAUSE, PAUSE_AUDIT, CANCEL_SCHEDULE, START_RESTART, WAIT_RESTART, START_FOR_RETIRE and WAIT_RETIRE_READY.'
    op.update({'target_revision':expected,'deadline':{'type':'integer'},'period_estimate':ref('Object'),'cumulative_milliusd':{'type':'integer'},'automatic':{'type':'boolean'}})
    env=schemas['Environment']['properties'];env['status']['enum']+=['PAUSING','PAUSED','RESTARTING']
    env.update({'operating_schema':{'type':'integer','enum':[2]},'operating_policy':ref('OperatingPolicyRead'),'lifecycle_revision':expected,
        'estimated_committed_milliusd':{'type':'integer'},'schedule_pending':{'type':'boolean'},'cost_plan':ref('Object'),
        'first_period_estimate':ref('Object'),'last_period_estimate':ref('Object'),'pause_report':ref('Object'),'renewal_blocked_reason':ref('Object')})
