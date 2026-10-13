"""Server-owned registration policy and idempotent Cognito-sub profiles."""
from .errors import HubError

DEFAULTS_KEY = 'S#SIGNUP_DEFAULTS'

def defaults(service):
    row = service.store.get(DEFAULTS_KEY) or {}
    return {'max_active': min(int(row.get('max_active', service.user_limit)), service.total_limit),
            'budget_milliusd': min(int(row.get('budget_milliusd', service.budget)), service.budget)}

def set_defaults(service, actor, body):
    from .service import expected, retry
    if (set(body) != {'max_active', 'budget_milliusd'} or type(body['max_active']) is not int
        or not 0 <= body['max_active'] <= service.total_limit or type(body['budget_milliusd']) is not int
        or not 0 <= body['budget_milliusd'] <= service.budget):
        raise HubError('INVALID_INPUT', 'Quota mặc định phải nằm trong hạn mức toàn hệ thống.')
    def commit():
        row = service.store.get(DEFAULTS_KEY) or {'pk': DEFAULTS_KEY, 'kind': 'signup_defaults'}
        row.update(body, updated_by=actor, updated_at=int(service.clock()))
        service.store.commit([row], expected(row))
        return defaults(service)
    return retry(commit)

def ensure_profile(service, identity):
    """Only verified provider attributes enter storage; never reset an existing quota."""
    from .service import expected, retry
    if not identity['email_verified']:
        raise HubError('EMAIL_NOT_VERIFIED', 'Xác thực email trước khi quản lý môi trường.', 403)
    owner = identity['owner_id']
    def commit():
        profile = service.store.get('U#' + owner)
        if profile and profile.get('enabled') is False:
            raise HubError('ACCOUNT_DISABLED', 'Tài khoản đã bị khóa.', 403)
        quota = service.store.get('Q#' + owner)
        policy = defaults(service)
        rows = []
        if profile is None:
            profile = {'pk': 'U#' + owner, 'kind': 'account', 'owner_id': owner,
                       'cognito_sub': owner, 'email': identity['email'], 'email_verified': True,
                       'role': 'user', 'enabled': True, 'created_at': int(service.clock())}
            rows.append(profile)
        elif profile.get('email') != identity['email'] or not profile.get('email_verified') or profile.get('cognito_sub') != owner:
            profile.update(email=identity['email'], email_verified=True, cognito_sub=owner)
            rows.append(profile)
        if quota is None:
            quota = {'pk': 'Q#' + owner, 'active': 0, 'reserved_milliusd': 0, **policy,
                     'initialized_at': int(service.clock())}
            rows.append(quota)
        if rows:
            expected_rows = expected(*rows)
            service.store.commit(rows, expected_rows)
        return {'owner_id': owner, 'email': identity['email'], 'email_verified': True,
                'role': profile.get('role', 'user'), 'limits': service.limits(owner)}
    return retry(commit)
