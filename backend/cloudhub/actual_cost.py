"""Read tagged gross costs; never substitute missing billing data with zero."""
from datetime import datetime,timezone
from decimal import Decimal,ROUND_HALF_UP

class ActualCosts:
    def __init__(self, enabled=False, client=None):
        self.enabled=enabled;self.client=client

    def report(self, env, now):
        base={'source':'AWS Cost Explorer','metric':'UnblendedCost','currency':'USD',
            'checked_at':int(now),'estimate_milliusd':int(env.get('estimate',{}).get('total_milliusd',0)),
            'final':False,'coverage':'Chỉ chi phí gắn tag cloudhub:environment của môi trường; không phân bổ hạ tầng chung hoặc credits. Dữ liệu theo ngày UTC có độ trễ.',
            'actual_milliusd':None,'difference_milliusd':None}
        if not self.enabled:return {**base,'status':'NOT_CONFIGURED','message':'Chưa bật Cost Explorer và tag phân bổ chi phí.'}
        if self.client is None:
            import boto3
            from botocore.config import Config
            self.client=boto3.client('ce',region_name='us-east-1',config=Config(connect_timeout=2,read_timeout=10,retries={'total_max_attempts':1}))
        try:
            tags=self.client.list_cost_allocation_tags(TagKeys=['cloudhub:environment'],Status='Active',Type='UserDefined',MaxResults=100)
            if not any(t.get('TagKey')=='cloudhub:environment' and t.get('Status')=='Active' for t in tags.get('CostAllocationTags',[])):
                return {**base,'status':'TAGS_INACTIVE','message':'Tag cloudhub:environment chưa được kích hoạt trong Billing.'}
            start=datetime.fromtimestamp(int(env['created_at']),timezone.utc).date().isoformat()
            end=datetime.fromtimestamp(now,timezone.utc).date().isoformat()
            base.update(period_start=start,period_end_exclusive=end)
            if start>=end:return {**base,'status':'PENDING','message':'Chưa có ngày UTC hoàn tất để đối soát.'}
            args={'TimePeriod':{'Start':start,'End':end},'Granularity':'DAILY','Metrics':['UnblendedCost'],
                  'Filter':{'Tags':{'Key':'cloudhub:environment','Values':[env['environment_id']],'MatchOptions':['EQUALS']}},
                  'GroupBy':[{'Type':'DIMENSION','Key':'SERVICE'}]}
            total=Decimal(0);days=[];found=False
            for _ in range(50):
                page=self.client.get_cost_and_usage(**args)
                for period in page['ResultsByTime']:
                    subtotal=Decimal(0)
                    for group in period.get('Groups',[]):
                        metric=group['Metrics']['UnblendedCost']
                        if metric['Unit']!='USD':raise ValueError('Unexpected currency')
                        found=True;subtotal+=Decimal(metric['Amount'])
                    total+=subtotal
                    days.append({'date':period['TimePeriod']['Start'],'milliusd':int((subtotal*1000).quantize(Decimal('1'),rounding=ROUND_HALF_UP)),'estimated':bool(period.get('Estimated'))})
                if not page.get('NextPageToken'):break
                args['NextPageToken']=page['NextPageToken']
            else:raise ValueError('Cost pagination exceeded bound')
            if not found:return {**base,'status':'PENDING','message':'AWS chưa trả dữ liệu chi phí gắn tag; chưa kết luận chi phí bằng 0.','days':days}
            amount=int((total*1000).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
            return {**base,'status':'PROVISIONAL','actual_milliusd':amount,'difference_milliusd':amount-base['estimate_milliusd'],
                    'days':days,'message':'Chi phí AWS ghi nhận đến hết ngày trước; có thể tiếp tục thay đổi và phát sinh phí bản sao giữ lại.'}
        except Exception as exc:
            return {**base,'status':'UNAVAILABLE','error_code':getattr(exc,'response',{}).get('Error',{}).get('Code',type(exc).__name__),
                    'message':'Không đọc được dữ liệu Billing. Không suy ra chi phí bằng 0.'}
