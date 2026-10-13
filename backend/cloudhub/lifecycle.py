"""Shared renewable lifecycle; processors never implement renewal or its budget decisions."""
from decimal import Decimal
from .errors import HubError
from .operating_policy import current_policy,revision,validate_policy,PAUSABLE

class LifecycleMixin:
    def _committed(self,env):return int(env.get('estimated_committed_milliusd',env.get('estimate',{}).get('total_milliusd',0)))

    def _charge(self,env,minutes,policy=None):
        self.refresh_pricing()
        policy=policy or current_policy(env)
        user=self.store.get('Q#'+env['owner_id']) or {'pk':'Q#'+env['owner_id'],'active':0,'reserved_milliusd':0}
        glob=self.store.get('Q#GLOBAL') or {'pk':'Q#GLOBAL','active':0,'reserved_milliusd':0}
        profile=self.store.get('U#'+env['owner_id']);rows=[user,glob]+([profile] if profile else [])
        def deny(code,message,status=409):
            error=HubError(code,message,status);error.fence_rows=rows;raise error
        quote=self.estimator.estimate(env['template_id'],Decimal(minutes)/60,renewal=True,backup=False)
        if not quote['approved']:deny('PRICES_UNAPPROVED','Chưa có đơn giá hợp lệ cho kỳ tiếp theo.')
        amount=quote['total_milliusd'];committed=self._committed(env)
        if policy['mode']=='AUTO_RENEW' and (amount<=0 or committed+amount>policy['budget_limit_milliusd']):
            deny('CUMULATIVE_BUDGET_EXCEEDED','Dự toán cộng dồn của môi trường đã chạm giới hạn đã xác nhận.')
        if user['active']>user.get('max_active',self.user_limit) or glob['active']>self.total_limit:
            deny('QUOTA_EXCEEDED','Hạn mức hiện tại không cho phép tiếp tục kỳ vận hành.')
        if profile and profile.get('enabled') is False:deny('ACCOUNT_DISABLED','Tài khoản đã bị quản trị khóa.',403)
        if glob['reserved_milliusd']+amount>self.budget or user.get('reserved_milliusd',0)+amount>user.get('budget_milliusd',self.budget):
            deny('BUDGET_EXCEEDED','Ngân sách dự toán người dùng hoặc hệ thống không đủ cho kỳ tiếp theo.')
        user['reserved_milliusd']=user.get('reserved_milliusd',0)+amount;glob['reserved_milliusd']+=amount
        return quote,rows

    def _management(self,env,key,action,body,phase,extra_rows=(),fields=None,idempotency_scope=None):
        from .service import ident,record,expected
        owner=env['owner_id'];ipk,fp,old=self.idempotency(owner,key,env['environment_id']+'/'+(idempotency_scope or action),body)
        if old:return self.owned('O',old['operation_id'],owner)
        oid=ident('op-',ipk);now=int(self.clock())
        env['lock']=oid
        op=record('operation',oid,owner,operation_id=oid,job_id=oid,environment_id=env['environment_id'],
            action=action,status='QUEUED',phase=phase,attempts=0,events=[],created_at=now,
            target_revision=revision(env),**(fields or {}))
        if phase=='SYNC_SCHEDULE':env['schedule_sync_operation_id']=oid
        rows=[env,op,{'pk':ipk,'fingerprint':fp,'operation_id':oid},*extra_rows]
        self.store.commit(rows,expected(*rows));return self.store.get(op['pk'])

    def _assert_mutable(self,env,body,states={'READY'}):
        if env['status'] not in states or env.get('lock'):
            raise HubError('OPERATION_LOCKED','Môi trường đang xử lý hoặc trạng thái không cho phép thay đổi.',409)
        if 'expected_revision' in body and (type(body['expected_revision']) is not int or body['expected_revision']!=revision(env)):
            raise HubError('STALE_REVISION','Chính sách hoặc deadline đã đổi. Tải lại trước khi xác nhận.',409)

    def _renew_transaction(self,env,key,body,minutes,automatic=False,extra_rows=None,quote=None):
        old_deadline=env['deadline'];now=int(self.clock())
        if quote is None:quote,extra_rows=self._charge(env,minutes)
        env.update(deadline=max(old_deadline,now)+minutes*60,lifecycle_revision=revision(env)+1,
            operating_policy=current_policy(env),operating_schema=2,schedule_pending=True,expiry_pending=False,
            estimated_committed_milliusd=self._committed(env)+quote['total_milliusd'],renewal_count=int(env.get('renewal_count',0))+1)
        env['estimate']['total_milliusd']=env['estimated_committed_milliusd']
        env['last_period_estimate']=quote
        event={'previous_deadline':old_deadline,'deadline':env['deadline'],'minutes':minutes,'automatic':automatic,
            'period_estimate':quote,'cumulative_milliusd':env['estimated_committed_milliusd'],'processing_delay_seconds':max(0,now-old_deadline)}
        env['last_renewal']={**event,'at':now}
        return self._management(env,key,'renew',body,'SYNC_SCHEDULE',extra_rows or [],{**event,'schedule_pending':True})

    def renew(self,owner,eid,key,body):
        from .service import retry
        if set(body)-{'minutes','confirmed','expected_revision'} or body.get('confirmed') is not True or type(body.get('minutes')) is not int or not 5<=body['minutes']<=480:
            raise HubError('INVALID_INPUT','Xác nhận kỳ gia hạn từ 5 đến 480 phút và dự toán phát sinh.')
        def transaction():
            env=self.owned('E',eid,owner);_,_,old=self.idempotency(owner,key,eid+'/renew',body)
            if old:return self.owned('O',old['operation_id'],owner)
            self._assert_mutable(env,body)
            if env['deadline']<=int(self.clock()):raise HubError('INVALID_STATE','Đã đến mốc xử lý; không thể chen gia hạn sau khi hết hạn.',409)
            if env.get('schedule_pending'):raise HubError('SCHEDULE_PENDING','Cần đồng bộ lịch hiện tại trước khi gia hạn tiếp.',409)
            return self._renew_transaction(env,key,body,body['minutes'])
        return retry(transaction)

    def change_policy(self,owner,eid,key,body):
        self.refresh_pricing()
        from .service import retry
        if set(body)-{'operating_policy','expected_revision'} or 'operating_policy' not in body:
            raise HubError('INVALID_INPUT','Chỉ nhận chính sách vận hành và phiên bản hiện tại.')
        def transaction():
            env=self.owned('E',eid,owner);_,_,old=self.idempotency(owner,key,eid+'/change_policy',body)
            if old:return self.owned('O',old['operation_id'],owner)
            self._assert_mutable(env,body,{'READY','PAUSED'})
            if env['status']=='READY' and env['deadline']<=int(self.clock()):raise HubError('INVALID_STATE','Đã đến mốc xử lý; tải lại trạng thái trước khi đổi chế độ.',409)
            if env.get('schedule_pending'):raise HubError('SCHEDULE_PENDING','Cần đồng bộ lịch hiện tại trước khi đổi chế độ.',409)
            policy=validate_policy(env['template_id'],body['operating_policy'])
            flag='retirement_allowance_committed' if policy['ending_policy']=='BACKUP_RETIRE' else 'pause_holding_allowance_committed'
            amount=0;rows=[]
            if not env.get(flag,flag=='retirement_allowance_committed' and not env.get('operating_schema')):
                holding=self.estimator.holding(env['template_id'],policy['ending_policy'])
                if not holding['approved']:raise HubError('PRICES_UNAPPROVED','Chưa có dự toán lưu dữ liệu cho chính sách mới.',409)
                amount=holding['total_milliusd'];rows=list(self.quota(owner,amount,0));env[flag]=True
                env['estimated_committed_milliusd']=self._committed(env)+amount
                env['estimate']['total_milliusd']=env['estimated_committed_milliusd']
            if policy['mode']=='AUTO_RENEW' and (policy['budget_limit_milliusd']>self.budget or policy['budget_limit_milliusd']<self._committed(env)):
                raise HubError('CUMULATIVE_BUDGET_EXCEEDED','Giới hạn cộng dồn phải chứa cả dự toán đã cam kết và nằm trong ngân sách hệ thống.',409)
            previous=current_policy(env);env.update(operating_policy=policy,operating_schema=2,lifecycle_revision=revision(env)+1,schedule_pending=True)
            env['cost_plan']={**env.get('cost_plan',{}),'operating_policy':policy,
                'renewal_period':self.estimator.estimate(env['template_id'],Decimal(policy['period_minutes'])/60,renewal=True,backup=False),
                'after_shutdown':self.estimator.holding(env['template_id'],policy['ending_policy'])}
            return self._management(env,key,'change_policy',body,'SYNC_SCHEDULE',rows,fields={'previous_policy':previous,'operating_policy':policy,'deadline':env.get('deadline'),'additional_commitment_milliusd':amount})
        return retry(transaction)

    def _pause_transaction(self,env,key,body,automatic=False,extra_rows=()):
        if env['template_id'] not in PAUSABLE:raise HubError('PAUSE_UNSUPPORTED','Mẫu không hỗ trợ tạm dừng.',409)
        env.update(status='PAUSING',lifecycle_revision=revision(env)+1,expiry_pending=False)
        return self._management(env,key,'pause',body,'PAUSE',extra_rows,
            {'auto_termination':automatic,'ending_policy':'PAUSE','mandatory_backup':False},None if automatic else 'end')

    def end(self,owner,eid,key,body):
        from .service import retry
        if set(body)-{'confirmed','expected_revision'} or body.get('confirmed') is not True:
            raise HubError('POLICY_CONSENT_REQUIRED','Xác nhận tác động dữ liệu và tài nguyên trước khi kết thúc.')
        def transaction():
            env=self.owned('E',eid,owner);_,_,old=self.idempotency(owner,key,eid+'/end',body)
            if old:return self.owned('O',old['operation_id'],owner)
            self._assert_mutable(env,body,{'READY','PAUSED'})
            if current_policy(env)['ending_policy']=='PAUSE':
                if env['status']=='PAUSED':raise HubError('INVALID_STATE','Môi trường đã tạm dừng; dùng thu hồi nếu muốn xóa hạ tầng.',409)
                return self._pause_transaction(env,key,body)
            return self._enqueue_command(env,owner,eid,'destroy',key,body,idempotency_scope='end')
        return retry(transaction)

    def resume_paused(self,owner,eid,key,body):
        from .service import retry
        if set(body)-{'confirmed','expected_revision'} or body.get('confirmed') is not True:
            raise HubError('POLICY_CONSENT_REQUIRED','Xác nhận dự toán kỳ mới khi mở lại môi trường.')
        def transaction():
            env=self.owned('E',eid,owner);_,_,old=self.idempotency(owner,key,eid+'/resume_paused',body)
            if old:return self.owned('O',old['operation_id'],owner)
            self._assert_mutable(env,body,{'PAUSED'});policy=current_policy(env)
            if env['template_id'] not in PAUSABLE:raise HubError('PAUSE_UNSUPPORTED','Mẫu không hỗ trợ mở lại từ tạm dừng.',409)
            quote,rows=self._charge(env,policy['period_minutes'])
            env.update(status='RESTARTING',deadline=None,lifecycle_revision=revision(env)+1,schedule_pending=True,
                estimated_committed_milliusd=self._committed(env)+quote['total_milliusd'])
            env['estimate']['total_milliusd']=env['estimated_committed_milliusd'];env['last_period_estimate']=quote
            return self._management(env,key,'resume_paused',body,'START_RESTART',rows,
                {'period_estimate':quote,'period_minutes':policy['period_minutes'],'cumulative_milliusd':env['estimated_committed_milliusd']})
        return retry(transaction)

    def expire(self,eid,expected_deadline=None,expected_revision=None):
        from .service import retry,expected,record,ident
        def transaction():
            env=self.store.get('E#'+eid);now=int(self.clock())
            if not env or env.get('deadline') is None or env['deadline']>now:return None
            if expected_deadline is not None and env['deadline']!=expected_deadline:return None
            if expected_revision is not None and revision(env)!=expected_revision:return None
            if env['status'] not in {'READY','BACKING_UP'}:return None
            if env.get('lock'):
                env.update(expiry_pending=True,expiry_triggered_at=env.get('expiry_triggered_at',now));self.save(env);return None
            policy=current_policy(env);key='expiry_'+str(env['deadline'])+'_v'+str(revision(env));extra=[]
            if policy['mode']=='AUTO_RENEW':
                try:quote,extra=self._charge(env,policy['period_minutes'])
                except HubError as error:
                    env['renewal_blocked_reason']={'code':error.code,'message':error.message,'at':now,'ending_policy':policy['ending_policy']}
                    # Fence both quota records used in a denial decision; retry reevaluates if they change.
                    extra=error.fence_rows
                    nid=ident('notice-',eid,key);extra.append(record('notification',nid,env['owner_id'],notification_id=nid,
                        environment_id=eid,type='RENEWAL_BLOCKED',created_at=now,**env['renewal_blocked_reason']))
                else:return self._renew_transaction(env,key,{},policy['period_minutes'],True,extra,quote)
            env['expiry_triggered_at']=env.get('expiry_triggered_at',now)
            if policy['ending_policy']=='PAUSE':return self._pause_transaction(env,key,{},True,extra)
            return self._enqueue_command(env,env['owner_id'],eid,'destroy',key,{},due=True,extra_rows=extra)
        return retry(transaction)

    def renewal_history(self,owner,eid):
        self.owned('E',eid,owner)
        rows=sorted([row for row in self.store.list_owner(owner,'operation') if row.get('environment_id')==eid and row.get('action')=='renew'],key=lambda r:r['created_at'],reverse=True)
        return {'items':rows[:100],'total_count':len(rows),'truncated':len(rows)>100}

    def management_estimate(self,owner,eid,body):
        self.refresh_pricing()
        env=self.owned('E',eid,owner)
        if set(body)-{'action','minutes','operating_policy'} or body.get('action') not in {'renew','resume_paused','change_policy','end','destroy','backup'}:
            raise HubError('INVALID_INPUT','Chọn tác vụ quản lý hợp lệ để xem dự toán.')
        policy=current_policy(env);action=body['action'];minutes=body.get('minutes',int(policy['period_minutes']))
        if type(minutes) is not int or not 5<=minutes<=480:raise HubError('INVALID_INPUT','Chu kỳ từ 5 đến 480 phút.')
        if action=='change_policy':policy=validate_policy(env['template_id'],body.get('operating_policy'));minutes=policy['period_minutes']
        ending='BACKUP_RETIRE' if action=='destroy' else policy['ending_policy']
        holding=self.estimator.holding(env['template_id'],ending)
        period=self.estimator.estimate(env['template_id'],Decimal(minutes)/60,renewal=True,backup=False)
        amount=period['total_milliusd'] if action in {'renew','resume_paused'} else 0
        if action=='backup':period=self.estimator.estimate(env['template_id'],1);amount=period['total_milliusd']
        flag='retirement_allowance_committed' if ending=='BACKUP_RETIRE' else 'pause_holding_allowance_committed'
        if action in {'change_policy','end','destroy'} and not env.get(flag,flag=='retirement_allowance_committed' and not env.get('operating_schema')):
            amount=holding['total_milliusd']
        approved=holding['approved'] and period['approved']
        return {'approved':bool(approved),'action':action,'period':period,'after_shutdown':holding,
            'additional_commitment_milliusd':amount if approved else None,
            'cumulative_after_milliusd':self._committed(env)+amount if approved else None,
            'current_deadline':env.get('deadline'),'deadline_changes':action in {'renew','resume_paused'},
            'new_policy':policy,'budget_is_aws_billing_cap':False,
            'unknown':['Mở lại hoặc thu hồi bản tạm dừng cần chạy máy để kiểm dữ liệu/sao lưu; thời gian xử lý có thể phát sinh phí.']}

    def retry_automatic_operation(self,op):
        phase=op.get('failed_phase',op['phase'])
        if op['status']!='FAILED' or not (phase=='BACKUP' and op.get('auto_termination') or phase=='SYNC_SCHEDULE'):return None
        if int(op.get('automatic_retries',0))>=2:return None
        if self.workflow_terminal and not self.workflow_terminal(op):return None
        return self.control_operation(op['owner_id'],op['operation_id'],'retry',
            'automatic_retry_'+op['operation_id']+'_g'+str(op.get('generation',0)),{},automatic=True)

    def enqueue_schedule_repair(self,eid):
        from .service import retry
        def transaction():
            env=self.store.get('E#'+eid)
            if not env or env['status'] not in {'READY','PAUSED'} or not env.get('schedule_pending') or env.get('lock'):return None
            original=self.store.get('O#'+env.get('schedule_sync_operation_id',''))
            if original and original['status'] in {'QUEUED','RUNNING','FAILED'}:return None # Retry budget belongs to that durable operation.
            env['schedule_repair_sequence']=int(env.get('schedule_repair_sequence',0))+1
            return self._management(env,'repair_schedule_v'+str(revision(env))+'_r'+str(env['schedule_repair_sequence']),'change_policy',{},'SYNC_SCHEDULE',fields={'repair_only':True})
        return retry(transaction)
