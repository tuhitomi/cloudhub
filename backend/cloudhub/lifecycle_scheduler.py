"""One stable schedule per environment, full Get/Update configuration and revision fencing."""
import hashlib,json,re
from datetime import datetime,timezone
from .errors import HubError
from .operating_policy import revision

UPDATE_FIELDS={'Name','GroupName','ScheduleExpression','ScheduleExpressionTimezone','StartDate','EndDate',
    'State','Description','KmsKeyArn','FlexibleTimeWindow','Target','ActionAfterCompletion'}

def error_code(exc):return getattr(exc,'response',{}).get('Error',{}).get('Code','')

class LifecycleScheduler:
    def __init__(self,client,settings):self.client,self.settings=client,settings
    def name(self,env):
        if not re.fullmatch(r'env-[a-z0-9-]{1,45}',env['environment_id']):raise HubError('RESOURCE_SCOPE_MISMATCH','Mã môi trường không hợp lệ cho lịch.',409)
        return env['environment_id']+'-lifecycle'
    def _owned(self,config,env):
        target=config['Target']
        try:payload=json.loads(target['Input'])
        except (ValueError,KeyError):raise HubError('SCHEDULE_SCOPE_MISMATCH','Lịch không có dữ liệu CloudHub hợp lệ.',409) from None
        if (target['Arn']!=self.settings['expiry_arn'] or target['RoleArn']!=self.settings['scheduler_role']
            or payload.get('environment_id')!=env['environment_id']):
            raise HubError('SCHEDULE_SCOPE_MISMATCH','Lịch hoặc quyền thực thi nằm ngoài môi trường.',409)
    def schedule(self,env):
        name=self.name(env);group=self.settings['schedule_group']
        desired={'Name':name,'GroupName':group,'ScheduleExpression':'at('+datetime.fromtimestamp(int(env['deadline']),timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')+')',
            'ScheduleExpressionTimezone':'UTC','FlexibleTimeWindow':{'Mode':'OFF'},'State':'ENABLED',
            # Stable schedules must not auto-delete a newly scheduled next period after their old invocation.
            'ActionAfterCompletion':'NONE','Target':{'Arn':self.settings['expiry_arn'],'RoleArn':self.settings['scheduler_role'],
                'Input':json.dumps({'environment_id':env['environment_id'],'expected_deadline':int(env['deadline']),'expected_revision':revision(env)},sort_keys=True),
                'RetryPolicy':{'MaximumEventAgeInSeconds':3600,'MaximumRetryAttempts':3},'DeadLetterConfig':{'Arn':self.settings['dlq_arn']}}}
        token=hashlib.sha256((name+'|'+str(env['deadline'])+'|'+str(revision(env))+'|'+str(env.get('schedule_repair_sequence',0))).encode()).hexdigest()
        try:existing=self.client.get_schedule(Name=name,GroupName=group)
        except Exception as exc:
            if error_code(exc)!='ResourceNotFoundException':raise
            try:self.client.create_schedule(**desired,ClientToken=token)
            except Exception as conflict:
                if error_code(conflict)!='ConflictException':raise
                existing=self.client.get_schedule(Name=name,GroupName=group);self._owned(existing,env)
                self._update(existing,desired,token)
        else:
            self._owned(existing,env);self._update(existing,desired,token)
        env.update(schedule_name=name,schedule_synced_revision=revision(env),schedule_synced_deadline=int(env['deadline']))
    def _update(self,existing,desired,token):
        preserved={key:value for key,value in existing.items() if key in UPDATE_FIELDS}
        target=dict(existing['Target']);target['Input']=desired['Target']['Input']
        # Preserve retry policy, DLQ, KMS key, start/end dates, description and complete target configuration.
        preserved.update({key:desired[key] for key in ['Name','GroupName','ScheduleExpression','ScheduleExpressionTimezone','FlexibleTimeWindow','State','ActionAfterCompletion']})
        preserved['Target']=target
        self.client.update_schedule(**preserved,ClientToken=token)
    def cancel(self,env):
        names={self.name(env)}
        if env.get('deadline'):names.add(env['environment_id']+'-'+str(int(env['deadline'])))
        for name in sorted(names):
            try:existing=self.client.get_schedule(Name=name,GroupName=self.settings['schedule_group'])
            except Exception as exc:
                if error_code(exc)=='ResourceNotFoundException':continue
                raise
            self._owned(existing,env)
            self.client.delete_schedule(Name=name,GroupName=self.settings['schedule_group'],
                ClientToken=hashlib.sha256((name+'|cancel|'+str(revision(env))).encode()).hexdigest())
