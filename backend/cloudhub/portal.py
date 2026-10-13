"""Product controls; every mutation is fenced with the existing durable store."""
from copy import deepcopy
from .errors import HubError

RETRY_PHASES = {'READINESS', 'SCHEDULE', 'BACKUP', 'RESUME', 'WAIT_RESUME',
                'DELETE', 'AUDIT', 'WRITE_FILE', 'USE_DATA', 'DELETE_BACKUP',
                'EXPORT_BACKUP', 'COST','SYNC_SCHEDULE','PAUSE','PAUSE_AUDIT','CANCEL_SCHEDULE',
                'START_RESTART','WAIT_RESTART','START_FOR_RETIRE','WAIT_RETIRE_READY'}

class PortalMixin:
    def limits(self, owner):
        user = self.store.get('Q#' + owner) or {}
        glob = self.store.get('Q#GLOBAL') or {}
        limit = int(user.get('max_active', self.user_limit))
        budget = int(user.get('budget_milliusd', self.budget))
        return {'active': int(user.get('active', 0)), 'max_active': limit,
                'remaining_environments': max(0, min(limit-int(user.get('active',0)), self.total_limit-int(glob.get('active',0)))),
                'user_reserved_milliusd': int(user.get('reserved_milliusd',0)),
                'user_budget_milliusd': budget,
                'remaining_milliusd': max(0, min(budget-int(user.get('reserved_milliusd',0)), self.budget-int(glob.get('reserved_milliusd',0)))),
                'global_remaining_environments': max(0,self.total_limit-int(glob.get('active',0))),
                'global_remaining_milliusd': max(0,self.budget-int(glob.get('reserved_milliusd',0))),
                'budget_method': 'Reserved estimates; not an AWS billing limit'}

    def set_limits(self, actor, owner, body):
        from .service import retry, expected
        if set(body) != {'max_active','budget_milliusd'} or type(body['max_active']) is not int or not 0 <= body['max_active'] <= self.total_limit or type(body['budget_milliusd']) is not int or not 0 <= body['budget_milliusd'] <= self.budget:
            raise HubError('INVALID_INPUT','Hạn mức phải nằm trong hạn mức toàn hệ thống.')
        if owner == 'GLOBAL' or not owner:
            raise HubError('INVALID_INPUT','Người dùng không hợp lệ.')
        def commit():
            row=self.store.get('Q#'+owner) or {'pk':'Q#'+owner,'active':0,'reserved_milliusd':0}
            row.update(body,updated_by=actor,updated_at=int(self.clock()))
            self.store.commit([row],expected(row))
            return self.limits(owner)
        return retry(commit)

    def notifications(self, owner):
        now=int(self.clock()); items=[]
        for saved in self.store.list_owner(owner,'notification'):
            if saved.get('type')=='RENEWAL_BLOCKED':
                items.append({'notification_id':saved['notification_id'],'environment_id':saved['environment_id'],
                    'type':saved['type'],'code':saved['code'],'message':saved['message'],
                    'ending_policy':saved['ending_policy'],'created_at':saved['created_at'],'remaining_seconds':0})
        for env in self.store.list_owner(owner,'environment'):
            if env['status'] in {'READY','BACKING_UP'} and env.get('deadline') and 0 < int(env['deadline'])-now <= 900:
                from .operating_policy import current_policy
                policy=current_policy(env)
                ending='tạm dừng và giữ dữ liệu' if policy['ending_policy']=='PAUSE' else 'sao lưu rồi thu hồi'
                message=('Sắp đến mốc kiểm tra gia hạn. Nếu không đủ điều kiện, hệ thống sẽ '+ending+'.'
                         if policy['mode']=='AUTO_RENEW' else 'Sắp đến mốc tự động '+ending+'.')
                items.append({'notification_id':env['environment_id']+'-'+str(env['deadline']),
                    'environment_id':env['environment_id'],'deadline':int(env['deadline']),
                    'remaining_seconds':int(env['deadline'])-now,'type':'EXPIRY_SOON',
                    'message':message})
        return {'items':items,'warning_minutes':15}

    def remind(self, eid):
        from .service import record, expected, retry
        def commit():
            env=self.store.get('E#'+eid); now=int(self.clock())
            if not env or env['status'] not in {'READY','BACKING_UP'} or not env.get('deadline') or not 0 < env['deadline']-now <= 900 or env.get('reminded_deadline')==env['deadline']:
                return None
            nid=eid+'-'+str(env['deadline'])
            item=record('notification',nid,env['owner_id'],notification_id=nid,type='EXPIRY_SOON',
                        environment_id=eid,deadline=env['deadline'],created_at=now)
            env['reminded_deadline']=env['deadline']
            self.store.commit([env,item],expected(env,item));return item
        return retry(commit)

    def control_operation(self, owner, oid, action, key, body,automatic=False):
        from .service import retry, expected, record, ident
        if body or action not in {'retry','cancel'}:
            raise HubError('INVALID_INPUT','Thao tác tác vụ không hợp lệ.')
        def commit():
            op=self.owned('O',oid,owner)
            ipk,fp,old=self.idempotency(owner,key,oid+'/'+action,{})
            if old:return self.owned('O',oid,owner)
            if op['status']!='FAILED':
                raise HubError('INVALID_STATE','Chỉ thử lại hoặc đóng tác vụ đã thất bại; tác vụ đang chạy phải hoàn tất trước.',409)
            if self.workflow_terminal and not self.workflow_terminal(op):
                raise HubError('WORKFLOW_RUNNING','Workflow vẫn đang kết thúc. Thử lại sau ít giây.',409)
            env=self.owned('E',op['environment_id'],owner)
            if env.get('lock'):
                raise HubError('OPERATION_LOCKED','Môi trường đang có tác vụ khác.',409)
            rows=[op,{'pk':ipk,'fingerprint':fp,'operation_id':oid}]
            if action=='cancel':
                op.update(status='CANCELLED',cancelled_at=int(self.clock()),cancel_note='Đóng tác vụ lỗi; giữ nguyên dữ liệu và tài nguyên. Thu hồi là thao tác riêng.')
            else:
                phase=op.get('failed_phase',op['phase'])
                construction_retry=(phase in {'CREATE','WAIT_CREATE'} and op['action']=='create'
                        and env['template_id'] in {'T03','T04','T06','T08','T09'})
                if construction_retry:
                    phase='CREATE'
                if (phase not in RETRY_PHASES and not construction_retry) or (op.get('automatic_retries',0)>=2 if automatic else op.get('manual_retries',0)>=3):
                    raise HubError('RETRY_UNAVAILABLE','Không thể thử lại bước này. Kiểm tra/thu hồi nguồn trước khi tạo mới, hoặc đã đạt 3 lần thử.',409)
                rp=self.store.get('R#'+op['recovery_point']) if op.get('recovery_point') else None
                if op['action'] in {'delete_backup','export_backup'}:
                    if not rp or rp.get('restore_lock') or rp.get('export_lock') or rp.get('delete_lock') or op['action']=='delete_backup' and rp.get('retirement_lock'):
                        raise HubError('RECOVERY_LOCKED','Bản sao đang được sử dụng.',409)
                    if op['action']=='export_backup' and (rp['status']!='AVAILABLE' or rp['retain_until']<=self.clock()):
                        raise HubError('RECOVERY_UNAVAILABLE','Bản sao không còn cho tải.',409)
                    rp['delete_lock' if op['action']=='delete_backup' else 'export_lock']=oid
                    if op['action']=='delete_backup':rp['status']='DELETING'
                elif op['action']=='cost':
                    if env.get('cost_lock'):raise HubError('OPERATION_LOCKED','Đang có đối soát khác.',409)
                    env['cost_lock']=oid;rows.append(env)
                else:
                    if env['status']=='DELETED' and op['action']!='audit' and op['action']!='cost':
                        raise HubError('INVALID_STATE','Nguồn đã thu hồi, không thử lại thao tác dữ liệu.',409)
                    env['lock']=oid
                    if op['action']=='destroy' and phase=='DELETE' and op.get('recovery_point'):
                        if (not rp or rp['status']!='AVAILABLE' or rp['retain_until']<=self.clock()
                                or rp.get('delete_lock') or rp.get('owner_id')!=owner
                                or rp.get('environment_id')!=env['environment_id']
                                or rp.get('retirement_lock') not in {None,oid}):
                            raise HubError('FINAL_BACKUP_UNAVAILABLE','Bản sao cuối cùng không còn hợp lệ. Giữ nguồn; tạo tác vụ thu hồi mới để sao lưu lại.',409)
                        rp['retirement_lock']=oid
                    if op['action']=='restore':
                        if not rp or rp['status']!='AVAILABLE' or rp['retain_until']<=self.clock() or rp.get('restore_lock') or rp.get('export_lock') or rp.get('delete_lock'):
                            raise HubError('RECOVERY_UNAVAILABLE','Không thể dùng lại bản sao.',409)
                        rp['restore_lock']=oid
                    env['status']={'backup':'BACKING_UP','destroy':'RETIRING','create':'PROVISIONING','restore':'PROVISIONING'}.get(op['action'],env['status'])
                    rows.append(env)
                if rp:rows.append(rp)
                if phase=='BACKUP' and op['action'] in {'backup','destroy'}:
                    # A resumed source may have changed. Never reuse its old checksum,
                    # file cursor or native snapshot for the next retirement attempt.
                    from .catalog import SCOPES
                    quote=self.estimator.estimate(env['template_id'],1)
                    if not quote['approved']:raise HubError('PRICES_UNAPPROVED','Chưa phê duyệt dự toán bản sao.',409)
                    rows.extend(self.quota(owner,quote['total_milliusd'],0))
                    env['estimated_committed_milliusd']=self._committed(env)+quote['total_milliusd']
                    env['estimate']['total_milliusd']=env['estimated_committed_milliusd']
                    rid=ident('rp-',oid,'retry',str(op.get('generation',0)+1));now=int(self.clock())
                    fresh=record('recovery',rid,owner,recovery_point=rid,environment_id=env['environment_id'],template_id=env['template_id'],status='PENDING',source=env.get('data_source') or env.get('bucket_name') or env.get('data_volume_id'),created_at=now,retain_until=now+7*86400,scope=SCOPES[env['template_id']],restore_verified=False,retry_of=op.get('recovery_point'),retirement_lock=oid if op['action']=='destroy' else None)
                    rows.append(fresh);op['recovery_point']=rid
                    for name in ['backup_job_id','backup_cursor','backup_verify_cursor']:op.pop(name,None)
                op['phase']=phase;op['status']='QUEUED';op['attempts']=0
                if automatic:op['automatic_retries']=op.get('automatic_retries',0)+1
                else:op['manual_retries']=op.get('manual_retries',0)+1
                op['generation']=op.get('generation',0)+1
                op['previous_error']=op.pop('error',None)
                # SSM commands are terminal after failure; issue a fresh command.
                for name in list(op):
                    if name.endswith('_command') or name.startswith(('warehouse_','efs_bootstrap_','efs_ready_','efs_restore_copy_','efs_freeze_','efs_manifest_','efs_write_','efs_read_other_')):op.pop(name)
            self.store.commit(rows,expected(*rows));return self.store.get(op['pk'])
        return retry(commit)

    def backup_command(self, owner, rid, action, key, body):
        from .service import record, ident, retry, expected
        if body or action not in {'delete_backup','export_backup'}:
            raise HubError('INVALID_INPUT','Thao tác bản sao không hợp lệ.')
        def commit():
            rp=self.owned('R',rid,owner)
            ipk,fp,old=self.idempotency(owner,key,rid+'/'+action,{})
            if old:return self.owned('O',old['operation_id'],owner)
            if action=='delete_backup' and rp.get('retirement_lock'):
                raise HubError('RECOVERY_LOCKED','Bản sao cuối cùng được giữ đến khi xác nhận thu hồi sạch môi trường.',409)
            if rp.get('restore_lock') or rp.get('export_lock') or rp.get('delete_lock') or rp['status'] not in ({'AVAILABLE','DELETE_FAILED'} if action=='delete_backup' else {'AVAILABLE'}):
                raise HubError('RECOVERY_LOCKED','Bản sao đang được xử lý hoặc không sẵn sàng.',409)
            if action=='export_backup' and rp['retain_until']<=self.clock():
                raise HubError('RECOVERY_UNAVAILABLE','Bản sao đã hết hạn.',409)
            oid=ident('op-',ipk)
            op=record('operation',oid,owner,operation_id=oid,job_id=oid,environment_id=rp['environment_id'],
                action=action,status='QUEUED',phase='DELETE_BACKUP' if action=='delete_backup' else 'EXPORT_BACKUP',
                recovery_point=rid,created_at=int(self.clock()),attempts=0,events=[])
            rp['delete_lock' if action=='delete_backup' else 'export_lock']=oid
            if action=='delete_backup':rp['status']='DELETING'
            rows=[rp,op,{'pk':ipk,'fingerprint':fp,'operation_id':oid}]
            self.store.commit(rows,expected(*rows));return self.store.get(op['pk'])
        return retry(commit)

    def cost_command(self, owner, eid, key, body):
        from .service import record, ident, retry, expected
        if body:raise HubError('INVALID_INPUT','Đối soát dùng nội dung rỗng.')
        def commit():
            env=self.owned('E',eid,owner)
            ipk,fp,old=self.idempotency(owner,key,eid+'/cost',{})
            if old:return self.owned('O',old['operation_id'],owner)
            if env.get('cost_lock') or self.clock()-env.get('cost_requested_at',-86400)<86400:
                raise HubError('COST_THROTTLED','Đối soát tối đa một lần mỗi 24 giờ cho mỗi môi trường.',409)
            oid=ident('op-',ipk)
            op=record('operation',oid,owner,operation_id=oid,job_id=oid,environment_id=eid,
                action='cost',status='QUEUED',phase='COST',created_at=int(self.clock()),attempts=0,events=[])
            env['cost_lock']=oid;env['cost_requested_at']=int(self.clock())
            rows=[env,op,{'pk':ipk,'fingerprint':fp,'operation_id':oid}]
            self.store.commit(rows,expected(*rows));return self.store.get(op['pk'])
        return retry(commit)
