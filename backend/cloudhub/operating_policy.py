"""Explicit lifecycle consent; old environments retain automatic backup-retirement."""
from decimal import Decimal
from .errors import HubError

PAUSABLE={'T01','T05'}
MODES={'AUTO_RENEW','AUTO_OFF'}
ENDING_POLICIES={'PAUSE','BACKUP_RETIRE'}

def legacy_policy(env):
    return {'mode':'AUTO_OFF','period_minutes':int(env.get('duration_hours',1))*60,
            'ending_policy':'BACKUP_RETIRE','budget_limit_milliusd':None,'confirmed':True,'source':'legacy_no_auto_renew'}

def current_policy(env):
    return env.get('operating_policy') or legacy_policy(env)

def revision(env):return int(env.get('lifecycle_revision',0))

def validate_policy(template_id,value):
    keys={'mode','period_minutes','ending_policy','budget_limit_milliusd','confirmed'}
    if not isinstance(value,dict) or set(value)!=keys or value.get('confirmed') is not True:
        raise HubError('POLICY_CONSENT_REQUIRED','Chọn và xác nhận chế độ, chu kỳ và chính sách kết thúc.')
    if value['mode'] not in MODES or value['ending_policy'] not in ENDING_POLICIES:
        raise HubError('INVALID_INPUT','Chế độ hoặc chính sách kết thúc không hợp lệ.')
    if type(value['period_minutes']) is not int or not 5<=value['period_minutes']<=480:
        raise HubError('INVALID_INPUT','Chu kỳ vận hành từ 5 đến 480 phút.')
    if value['ending_policy']=='PAUSE' and template_id not in PAUSABLE:
        raise HubError('PAUSE_UNSUPPORTED','Mẫu này chỉ hỗ trợ sao lưu rồi thu hồi.',409)
    budget=value['budget_limit_milliusd']
    if value['mode']=='AUTO_RENEW':
        if type(budget) is not int or budget<=0:raise HubError('INVALID_INPUT','Chọn giới hạn dự toán cộng dồn cho tự động gia hạn.')
    elif budget is not None:raise HubError('INVALID_INPUT','Chế độ tự động tắt không nhận giới hạn gia hạn.')
    return value.copy()

def plan(estimator,template_id,estimate_hours,policy):
    if type(estimate_hours) is not int or not 1<=estimate_hours<=8:
        raise HubError('INVALID_INPUT','Khoảng xem dự toán từ 1 đến 8 giờ, độc lập chu kỳ vận hành.')
    period=Decimal(policy['period_minutes'])/60
    retiring=policy['ending_policy']=='BACKUP_RETIRE'
    first=estimator.estimate(template_id,period,backup=retiring)
    recurring=estimator.estimate(template_id,period,renewal=True,backup=False)
    holding=estimator.holding(template_id,policy['ending_policy'])
    pause_reserve=holding['total_milliusd'] if not retiring else 0
    approved=first['approved'] and recurring['approved'] and holding['approved']
    return {'template_id':template_id,'approved':bool(approved),'currency':'USD',
        'estimate_hours':estimate_hours,'operating_policy':policy,
        'preview':estimator.estimate(template_id,estimate_hours,backup=retiring),
        'first_period':first,'renewal_period':recurring,'after_shutdown':holding,
        'initial_commitment_milliusd':first['total_milliusd']+pause_reserve if approved else None,
        'total_milliusd':first['total_milliusd']+pause_reserve if approved else None,
        'terminal_allowance_in_initial_commitment':True,
        'budget_scope':'Cộng dồn từ lúc tạo: kỳ đầu + mọi gia hạn thủ công/tự động và mở lại + dự phòng dữ liệu sau kết thúc (7 ngày bản sao hoặc 24 giờ EBS tạm dừng). Không đặt lại khi đổi chế độ.',
        'budget_is_aws_billing_cap':False,
        'unknown':['Phí giữ môi trường tạm dừng sau 24 giờ, xử lý trễ, bản sao thêm và lưu lượng thực tế không được bảo đảm nằm trong giới hạn dự toán.']}
