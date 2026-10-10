"""UI product endpoints. Admin claims are revalidated against live Cognito."""
import json,os,re
from .errors import HubError

UNHANDLED=object()

def route(service, method, path, body, key, owner, admin, claims, query, public):
    accounts=None
    if path.startswith('/admin/') or method=='POST' and path=='/environments' and body.get('validation_run') is True:
        if not admin:raise HubError('FORBIDDEN','Cần quyền quản trị.',403)
        from .accounts import Accounts
        accounts=Accounts(os.environ['USER_POOL_ID'])
        accounts.assert_admin(owner,claims.get('username'))
    if method=='GET' and path=='/limits':return service.limits(owner),200
    if method=='GET' and path=='/notifications':return service.notifications(owner),200
    if method=='GET' and path=='/operations':
        return {'items':[public(o) for o in sorted(service.store.list_owner(owner,'operation'),key=lambda o:o.get('created_at',0),reverse=True)[:100]]},200
    if method=='POST' and re.fullmatch(r'/operations/[a-z0-9-]+/(retry|cancel)',path):
        _,oid,action=path.strip('/').split('/')
        return public(service.control_operation(owner,oid,action,key,body)),202 if action=='retry' else 200
    if method=='POST' and re.fullmatch(r'/recovery-points/[a-z0-9-]+/(delete|export)',path):
        _,rid,action=path.strip('/').split('/')
        return public(service.backup_command(owner,rid,'delete_backup' if action=='delete' else 'export_backup',key,body)),202
    if method=='GET' and re.fullmatch(r'/operations/[a-z0-9-]+/download',path):
        from .backup_export import download
        return download(service,owner,path.split('/')[2],json.loads(os.environ['DRIVER_SETTINGS'])),200
    if re.fullmatch(r'/environments/[a-z0-9-]+/cost',path):
        eid=path.split('/')[2]
        if method=='GET':return service.owned('E',eid,owner).get('cost_report',{'status':'NOT_REQUESTED','actual_milliusd':None}),200
        if method=='POST':return public(service.cost_command(owner,eid,key,body)),202
    if method=='GET' and path=='/admin/environments':
        return {'items':[public(e) for e in service.store.scan_kind('environment')]},200
    if method=='GET' and re.fullmatch(r'/admin/operations/[a-z0-9-]+',path):
        row=service.store.get('O#'+path.split('/')[-1])
        if not row:raise HubError('NOT_FOUND','Không tìm thấy tác vụ.',404)
        return public(row),200
    if method=='GET' and path=='/admin/users':
        result=accounts.list(query.get('cursor'))
        for user in result['items']:
            if user['owner_id']:
                user['limits']=service.limits(user['owner_id'])
                user['cognito_enabled']=user['enabled']
                local=service.store.get('U#'+user['owner_id'])
                if local and local.get('enabled') is False:user['enabled']=False
        return result,200
    if method=='POST' and path=='/admin/users':
        result=accounts.create_idempotent(service,owner,key,body)
        return result,201 if result['invitation_status']=='CREATED' else 200
    match=re.fullmatch(r'/admin/users/([a-f0-9-]{36})/(limits|enabled)',path)
    if method=='POST' and match:
        target,action=match.groups();accounts.find(target)
        if action=='limits':return service.set_limits(owner,target,body),200
        if set(body)!={'enabled'}:raise HubError('INVALID_INPUT','Cần trường enabled.')
        return accounts.set_enabled(owner,target,body['enabled'],service),200
    if method=='POST' and re.fullmatch(r'/admin/environments/[a-z0-9-]+/retire',path):
        if set(body)!={'reason'} or not isinstance(body['reason'],str) or not 10<=len(body['reason'].strip())<=500:
            raise HubError('INVALID_INPUT','Cần lý do thu hồi từ 10 đến 500 ký tự.')
        env=service.store.get('E#'+path.split('/')[3])
        if not env:raise HubError('NOT_FOUND','Không tìm thấy môi trường.',404)
        return public(service.command(env['owner_id'],env['environment_id'],'destroy',key,admin_actor=owner,reason=body['reason'])),202
    if method=='GET' and path=='/admin/notifications':
        from .accounts import subscription_status
        return subscription_status(os.getenv('ALERT_TOPIC_ARN'),os.getenv('ALERT_EMAIL')),200
    if method=='POST' and path=='/admin/notifications/test':
        if body!={'confirm_send':True}:raise HubError('INVALID_INPUT','Cần xác nhận gửi email kiểm tra.')
        from .accounts import subscription_status
        topic=os.getenv('ALERT_TOPIC_ARN');state=subscription_status(topic,os.getenv('ALERT_EMAIL'))
        if state['status']!='CONFIRMED':raise HubError('SNS_UNCONFIRMED','Người nhận chưa xác nhận đăng ký SNS.',409)
        import boto3
        result=boto3.client('sns').publish(TopicArn=topic,Subject='CloudHub - Kiem tra canh bao',Message='CloudHub: email kiểm tra cảnh báo vận hành. Hãy xác nhận đã nhận thư trong minh chứng triển khai.')
        return {'status':'PUBLISHED','message_id':result['MessageId'],'delivery_verified':False},200
    return UNHANDLED
