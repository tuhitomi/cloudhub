"""F03: no invented regional rates; signed-off deployment supplies actual units."""
import json
from decimal import Decimal, ROUND_CEILING
from .errors import HubError

REQUIRED_RATES = {"ec2_hour", "ebs_gib_month", "ipv4_hour", "snapshot_gib_month",
                  "transfer_gib", "logs_gib", "control_plane_session"}
S3_RATES = {"s3_gib_month", "s3_requests_1000", "cloudfront_transfer_gib", "cloudfront_requests_10000",
            "logs_gib", "control_plane_session"}
BASE_RATES={'logs_gib','control_plane_session','transfer_gib'}
RATE_REQUIREMENTS={
    'T01':REQUIRED_RATES,'T05':REQUIRED_RATES,'T02':S3_RATES,
    'T07':BASE_RATES|{'s3_gib_month','s3_requests_1000'},
    'T03':BASE_RATES|{'ec2_hour','ebs_gib_month','ipv4_hour','rds_hour','rds_gib_month','rds_snapshot_gib_month','secret_month','secret_requests_10000'},
    'T04':BASE_RATES|{'ec2_hour','ebs_gib_month','ipv4_hour','rds_hour','rds_gib_month','rds_snapshot_gib_month','secret_month','secret_requests_10000','alb_hour','alb_lcu_hour'},
    'T06':BASE_RATES|{'fargate_vcpu_hour','fargate_gib_hour','alb_hour','alb_lcu_hour','ipv4_hour','s3_gib_month','s3_requests_1000','ecr_gib_month'},
    'T08':BASE_RATES|{'ec2_hour','ebs_gib_month','ipv4_hour','efs_gib_month','efs_backup_gib_month','efs_restore_gib'},
    'T09':BASE_RATES|{'api_requests_million','lambda_requests_million','lambda_gib_second','dynamodb_reads_million','dynamodb_writes_million','dynamodb_gib_month','s3_gib_month','s3_requests_1000'},
    'T10':BASE_RATES|{'redshift_rpu_hour','redshift_gib_month','redshift_snapshot_gib_month','s3_gib_month','s3_requests_1000','secret_month','secret_requests_10000'}
}

class Estimator:
    def __init__(self, rates=None):
        self.rates = rates or {}

    def estimate(self, template_id, duration_hours, *, renewal=False, backup=True):
        required = RATE_REQUIREMENTS[template_id]
        approved = self.rates.get("approved", False) and required <= self.rates.get("units", {}).keys()
        assumptions = {"duration_hours": duration_hours, "provisioning_hours": "0.25",
                       "root_gib": 16, "data_gib": 8, "backup_gib": 8, "backup_retention_days": 7,
                       "transfer_gib": 1, "logs_gib": "0.1", "month_hours": 730,
                       "backup_outage_hours": "0.25"}
        if template_id == "T02":
            assumptions = {"duration_hours": duration_hours, "provisioning_hours": "0.5", "max_files": 32,
                "max_file_bytes": 8192, "max_total_bytes": 262144, "max_writes": 128,
                "source_with_versions_gib": "0.002", "backup_gib": "0.0003", "backup_retention_days": 7,
                "s3_requests": 2000, "cloudfront_transfer_gib": 1, "cloudfront_requests": 10000,
                "logs_gib": "0.1", "month_hours": 730, "backup_outage_hours": 0}
        if template_id not in {'T01','T02','T05'}:
            from .catalog import configuration
            assumptions={'duration_hours':duration_hours,'provisioning_and_backup_hours':'1.5','month_hours':730,
                'backup_retention_days':7,'transfer_gib':1,'logs_gib':'0.1','max_operations':128,'configuration':configuration(template_id),
                'cost_bound':'Usage envelope estimate, not a hard AWS billing cap. Redshift assumes continuous 8 RPU compute; ALB one LCU.'}
        if not approved:
            return {"template_id": template_id, "currency": "USD", "total_milliusd": None,
                    "approved": False, "assumptions": assumptions,
                    "unknown": ["Region và đơn giá chưa được phê duyệt. Không thể xác nhận tạo môi trường."],
                    "cost_data_delay": "Đây là dự toán; chi phí AWS thực tế có độ trễ."}
        u = {k: Decimal(str(v)) for k, v in self.rates["units"].items()}
        overhead = Decimal(0) if renewal else Decimal('0.5')
        retention = Decimal(0) if renewal or not backup else Decimal(7*24)/730
        hours = Decimal(duration_hours) + overhead
        if template_id == "T02":
            lines = {"storage": hours * Decimal("0.002") / 730 * u["s3_gib_month"],
                "backup": Decimal("0.0003") * retention * u["s3_gib_month"],
                "s3_requests": 2 * u["s3_requests_1000"], "transfer": u["cloudfront_transfer_gib"],
                "cloudfront_requests": u["cloudfront_requests_10000"], "logs": Decimal("0.1") * u["logs_gib"],
                "control_plane": u["control_plane_session"]}
        elif template_id in {'T01','T05'}:
            lines = {"compute": hours * u["ec2_hour"], "storage": hours * 24 / 730 * u["ebs_gib_month"],
                     "ipv4": hours * u["ipv4_hour"], "backup": Decimal(8) * retention * u["snapshot_gib_month"],
                     "transfer": u["transfer_gib"], "logs": Decimal("0.1") * u["logs_gib"],
                     "control_plane": u["control_plane_session"]}
        else:
            hours=Decimal(duration_hours)+(Decimal(0) if renewal else Decimal('1.5'))
            lines={'transfer':u['transfer_gib'],'logs':Decimal('.1')*u['logs_gib'],'control_plane':u['control_plane_session']}
            if template_id in {'T03','T04','T08'}:
                nodes=2 if template_id in {'T04','T08'} else 1
                lines.update(compute=hours*nodes*u['ec2_hour'],root_storage=hours*nodes*16/730*u['ebs_gib_month'],ipv4=hours*nodes*u['ipv4_hour'])
            if template_id in {'T03','T04'}:
                lines.update(database=hours*u['rds_hour'],db_storage=hours*20/730*u['rds_gib_month'],backup=20*retention*u['rds_snapshot_gib_month'],
                    secrets=hours/730*u['secret_month']+Decimal('.1')*u['secret_requests_10000'])
            if template_id in {'T04','T06'}:lines.update(load_balancer=hours*(u['alb_hour']+u['alb_lcu_hour']),alb_ipv4=hours*2*u['ipv4_hour'])
            if template_id in {'T06','T07','T09','T10'}:
                lines.update(s3_storage=hours*Decimal('.002')/730*u['s3_gib_month'],s3_backup=Decimal('.0003')*retention*u['s3_gib_month'],s3_requests=2*u['s3_requests_1000'])
            if template_id=='T06':lines.update(container=hours*(Decimal('.25')*u['fargate_vcpu_hour']+Decimal('.5')*u['fargate_gib_hour']),
                task_ipv4=hours*u['ipv4_hour'],image_storage=Decimal('.5')*(Decimal(duration_hours) if renewal else 24*7)/730*u['ecr_gib_month'])
            if template_id=='T08':lines.update(efs=hours*Decimal('.01')/730*u['efs_gib_month'],backup=Decimal('.02')*retention*u['efs_backup_gib_month'],restore_allowance=Decimal(0) if renewal else Decimal('.02')*u['efs_restore_gib'])
            if template_id=='T09':lines.update(api=Decimal('.01')*u['api_requests_million'],lambda_requests=Decimal('.01')*u['lambda_requests_million'],
                lambda_compute=Decimal(10000)*Decimal('.125')*Decimal('.5')*u['lambda_gib_second'],
                database=Decimal('.02')*(u['dynamodb_reads_million']+u['dynamodb_writes_million'])+hours*Decimal('.01')/730*u['dynamodb_gib_month'])
            if template_id=='T10':lines.update(warehouse=hours*8*u['redshift_rpu_hour'],warehouse_storage=hours/730*u['redshift_gib_month'],
                warehouse_backup=retention*u['redshift_snapshot_gib_month'],secrets=hours/730*u['secret_month']+Decimal('.1')*u['secret_requests_10000'])
        total = sum(lines.values()) * Decimal("1.2")
        assumptions.update(estimate_kind='renewal' if renewal else 'first_period',
            provisioning_allowance_included=not renewal,backup_allowance_included=bool(retention),
            usage_envelope='Lưu lượng, request, log và điều khiển là dự phòng riêng cho từng kỳ, không phải số đo thực tế.')
        if renewal:
            assumptions.update(provisioning_hours=0,provisioning_and_backup_hours=0,backup_outage_hours=0,backup_retention_days=0)
        elif not backup:assumptions['backup_retention_days']=0
        return {"template_id": template_id, "currency": "USD", "approved": True,
                "region": self.rates["region"], "price_updated_at": self.rates["updated_at"],
                "units": self.rates["units"], "lines": {k: str(v) for k, v in lines.items()},
                "assumptions": assumptions, "contingency_percent": 20,
                "total_milliusd": int((total * 1000).to_integral_value(rounding=ROUND_CEILING)),
                "unknown": ["Chi phí thực tế phụ thuộc lưu lượng, log và thời gian xử lý; quota không phải giới hạn hóa đơn AWS."],
                "cost_data_delay": "Billing/Cost Explorer có độ trễ; không dùng để khóa quota đồng thời."}

    def holding(self, template_id, ending_policy):
        """Known storage only; shared infrastructure and unmeasured usage remain explicit."""
        if ending_policy=='PAUSE':
            rates={'ebs_gib_month':Decimal(24)};hours=24
        elif template_id in {'T01','T05'}:rates={'snapshot_gib_month':Decimal(8)};hours=7*24
        elif template_id in {'T03','T04'}:rates={'rds_snapshot_gib_month':Decimal(20)};hours=7*24
        elif template_id=='T08':rates={'efs_backup_gib_month':Decimal('.02')};hours=7*24
        elif template_id=='T10':rates={'redshift_snapshot_gib_month':Decimal(1),'s3_gib_month':Decimal('.0003')};hours=7*24
        else:rates={'s3_gib_month':Decimal('.0003')};hours=7*24
        units=self.rates.get('units',{});approved=bool(self.rates.get('approved')) and set(rates)<=units.keys()
        total=sum(size*Decimal(hours)/730*Decimal(str(units[key])) for key,size in rates.items())*Decimal('1.2') if approved else None
        return {'approved':approved,'currency':'USD','ending_policy':ending_policy,'period_hours':hours,
            'total_milliusd':int((total*1000).to_integral_value(rounding=ROUND_CEILING)) if total is not None else None,
            'units':{key:units.get(key) for key in rates},'storage_gib':{key:str(size) for key,size in rates.items()},
            'price_updated_at':self.rates.get('updated_at'),'region':self.rates.get('region'),'contingency_percent':20,
            'continues_until_manual_retirement':ending_policy=='PAUSE',
            'unknown':['Giữ dữ liệu khi tạm dừng tiếp tục phát sinh phí sau 24 giờ cho đến khi thu hồi.','Chưa tính log lưu lâu, bản sao cũ, hạ tầng dùng chung và lưu lượng chưa đo.'] if ending_policy=='PAUSE' else ['Bản sao cũ, log và hạ tầng dùng chung có thể tiếp tục phát sinh phí; dữ liệu thực tế có thể lớn hơn giả định.']}
