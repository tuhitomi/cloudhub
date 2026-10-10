"""Cognito administration scoped to the configured pool; no passwords in responses."""
import re
from .errors import HubError

def email(value):
    if not isinstance(value,str) or len(value)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',value):
        raise HubError('INVALID_INPUT','Email không hợp lệ.')
    return value.strip().lower()

class Accounts:
    def __init__(self, pool, client=None):
        import boto3
        from botocore.config import Config
        self.pool=pool
        self.client=client or boto3.client('cognito-idp',config=Config(connect_timeout=.4,read_timeout=1,retries={'total_max_attempts':1}))

    def assert_admin(self, actor, username):
        if not username:raise HubError('FORBIDDEN','Cần phiên quản trị hợp lệ.',403)
        user=self.client.admin_get_user(UserPoolId=self.pool,Username=username)
        attrs={a['Name']:a['Value'] for a in user.get('UserAttributes',[])}
        groups=self.client.admin_list_groups_for_user(UserPoolId=self.pool,Username=user['Username'])['Groups']
        if attrs.get('sub')!=actor or not user.get('Enabled') or not any(g['GroupName']=='admin' for g in groups):
            raise HubError('FORBIDDEN','Tài khoản không còn quyền quản trị.',403)

    def public(self, user):
        attrs={a['Name']:a['Value'] for a in user.get('Attributes',user.get('UserAttributes',[]))}
        return {'username':user['Username'],'owner_id':attrs.get('sub'),'email':attrs.get('email',''),
                'email_verified':attrs.get('email_verified')=='true','enabled':bool(user.get('Enabled')),
                'status':user.get('UserStatus','UNKNOWN')}

    def list(self, cursor=None):
        args={'UserPoolId':self.pool,'Limit':50}
        if cursor:
            if not isinstance(cursor,str) or len(cursor)>4096:raise HubError('INVALID_INPUT','Cursor không hợp lệ.')
            args['PaginationToken']=cursor
        result=self.client.list_users(**args)
        return {'items':[self.public(u) for u in result['Users']],'next_cursor':result.get('PaginationToken')}

    def find(self, owner):
        # A pool lookup prevents a caller supplying another pool or arbitrary username.
        if not isinstance(owner,str) or not re.fullmatch(r'[a-f0-9-]{36}',owner):
            raise HubError('INVALID_INPUT','ID người dùng không hợp lệ.')
        rows=self.client.list_users(UserPoolId=self.pool,Filter='sub = "'+owner+'"',Limit=2)['Users']
        if len(rows)!=1:raise HubError('NOT_FOUND','Không tìm thấy tài khoản trong CloudHub.',404)
        return self.public(rows[0])

    def create(self, body):
        if set(body)!={'email','send_invitation'} or body['send_invitation'] is not True:
            raise HubError('INVALID_INPUT','Cần email và xác nhận gửi thư mời tạo tài khoản.')
        value=email(body['email'])
        # Deterministic email + Cognito UsernameExists prevents duplicate invitations.
        result=self.client.admin_create_user(UserPoolId=self.pool,Username=value,
            UserAttributes=[{'Name':'email','Value':value}],DesiredDeliveryMediums=['EMAIL'])
        return self.public(result['User'])

    def create_idempotent(self, service, actor, key, body):
        from .service import expected,retry
        from botocore.exceptions import ClientError
        if set(body)!={'email','send_invitation'} or body['send_invitation'] is not True:
            raise HubError('INVALID_INPUT','Cần email và xác nhận gửi thư mời tạo tài khoản.')
        value=email(body['email'])
        def claim():
            pk,fp,old=service.idempotency(actor,key,'admin/users',body)
            if old:return old
            row={'pk':pk,'fingerprint':fp,'status':'PENDING'}
            service.store.commit([row],expected(row));return service.store.get(pk)
        row=retry(claim)
        if row.get('result'):return row['result']
        try:
            result=self.create(body);result['invitation_status']='CREATED'
        except ClientError as exc:
            if exc.response['Error']['Code']!='UsernameExistsException':raise
            # A lost response or concurrent retry must never resend an invitation.
            result=self.public(self.client.admin_get_user(UserPoolId=self.pool,Username=value))
            if result['email'].lower()!=value:raise HubError('ACCOUNT_EXISTS','Tên tài khoản đã được dùng.',409)
            result['invitation_status']='ALREADY_EXISTS_NO_RESEND'
        def finish():
            current=service.store.get(row['pk'])
            if current.get('result'):return current['result']
            current.update(status='SUCCEEDED',result=result)
            service.store.commit([current],expected(current));return result
        return retry(finish)

    def set_enabled(self, actor, owner, enabled, service):
        if type(enabled) is not bool:raise HubError('INVALID_INPUT','enabled phải là boolean.')
        user=self.find(owner)
        groups=self.client.admin_list_groups_for_user(UserPoolId=self.pool,Username=user['username'])['Groups']
        if not enabled and (owner==actor or any(g['GroupName']=='admin' for g in groups)):
            raise HubError('ADMIN_PROTECTED','Không khóa tài khoản quản trị qua thao tác này.',409)
        from .service import expected,retry
        def local():
            row=service.store.get('U#'+owner) or {'pk':'U#'+owner,'kind':'account','owner_id':owner}
            row.update(enabled=enabled,updated_by=actor,updated_at=int(service.clock()))
            service.store.commit([row],expected(row))
        if not enabled:retry(local) # Fail closed even if the provider call fails.
        method=self.client.admin_enable_user if enabled else self.client.admin_disable_user
        method(UserPoolId=self.pool,Username=user['username'])
        if enabled:retry(local)
        user['enabled']=enabled;return user

def subscription_status(topic, receiver, client=None):
    if not topic or not receiver:return {'status':'NOT_CONFIGURED','email':receiver or ''}
    import boto3
    client=client or boto3.client('sns')
    matches=[];args={'TopicArn':topic}
    while True:
        page=client.list_subscriptions_by_topic(**args)
        matches.extend(s for s in page['Subscriptions'] if s.get('Protocol')=='email' and s.get('Endpoint')==receiver)
        if not page.get('NextToken'):break
        args['NextToken']=page['NextToken']
    confirmed=any(s.get('SubscriptionArn','').startswith('arn:') for s in matches)
    return {'email':receiver,'status':'CONFIRMED' if confirmed else 'PENDING_CONFIRMATION' if matches else 'NOT_SUBSCRIBED',
            'delivery_verified':False,'note':'Đã xác nhận đăng ký chưa đồng nghĩa đã kiểm tra nhận một cảnh báo thực tế.'}
