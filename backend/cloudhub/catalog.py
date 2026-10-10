"""F02: immutable approved configurations; validation gate is server-side."""
import os

NAMES = [
    ("T01", "Máy Linux", "Học Linux và chạy công cụ", ["EC2", "EBS"]),
    ("T02", "Website tĩnh", "Đăng website HTML", ["S3", "CloudFront"]),
    ("T03", "Web và cơ sở dữ liệu", "Ứng dụng có dữ liệu", ["EC2", "RDS"]),
    ("T04", "Web chịu tải cao", "Thử nghiệm phân phối tải", ["ALB", "Auto Scaling", "RDS"]),
    ("T05", "Website trên máy chủ", "Website trên Linux", ["EC2", "web server"]),
    ("T06", "Ứng dụng Docker", "Chạy image được duyệt", ["ECS Fargate"]),
    ("T07", "Kho file và chia sẻ", "Chia sẻ file có thời hạn", ["S3"]),
    ("T08", "Ổ đĩa dùng chung", "Chia sẻ dữ liệu hai máy", ["EFS", "EC2"]),
    ("T09", "API serverless", "API và dữ liệu theo yêu cầu", ["API Gateway", "Lambda", "DynamoDB"]),
    ("T10", "Kho dữ liệu phân tích", "Phân tích dữ liệu mẫu", ["Redshift Serverless", "S3"]),
]
IMPLEMENTED = {'T01','T02','T03','T04','T05','T06','T07','T08','T09','T10'}
S3_CONFIG = {"max_files": 32, "max_file_bytes": 8192, "max_total_bytes": 262144, "max_writes": 128}
SCOPES={
    'T01':'EBS /data sau dừng máy; không gồm root, RAM hoặc dữ liệu ngoài /data',
    'T05':'EBS /data và nội dung web trong /data; không gồm root/RAM',
    'T02':'File S3 hiện hành, checksum SHA-256; không gồm lịch sử phiên bản hay cấu hình CloudFront',
    'T07':'File S3 hiện hành, checksum SHA-256; không giữ hiệu lực liên kết chia sẻ cũ',
    'T03':'PostgreSQL: snapshot RDS và checksum bản ghi minh họa; không gồm root EC2',
    'T04':'PostgreSQL: snapshot RDS và checksum; ALB/ASG/EC2 được dựng mới',
    'T06':'S3 được đăng ký làm dữ liệu bền vững; không gồm filesystem tạm của container, RAM hay image',
    'T08':'EFS sau khóa ghi cả hai máy; checksum /records; không gồm root EC2',
    'T09':'Bản ghi người dùng DynamoDB, đọc nhất quán và SHA-256; không gồm nhật ký idempotency của môi trường cũ',
    'T10':'Snapshot namespace Redshift và file S3 hiện hành; checksum dữ liệu truy vấn, không gồm kết quả truy vấn tạm'
}

def configuration(code):
    if code in {'T02','T07'}:return S3_CONFIG.copy()
    if code in {'T01','T05'}:return {'instance_type':'t3.micro','root_gib':16,'data_gib':8}
    if code in {'T03','T04'}:return {'instance_type':'t3.micro','root_gib':16,'db_class':'db.t3.micro','db_gib':20,'min_instances':1,'max_instances':2 if code=='T04' else 1,'max_records':32,'max_record_bytes':256}
    if code=='T06':return {**S3_CONFIG,'cpu':256,'memory_mib':512,'tasks':1,'image':'operator-approved ECR digest'}
    if code=='T08':return {'instance_type':'t3.micro','root_gib':16,'nodes':2,'efs_encrypted':True,'max_records':32,'max_record_bytes':256}
    if code=='T09':return {'billing':'PAY_PER_REQUEST','max_records':32,'max_record_bytes':256,'lambda_memory_mib':128}
    if code=='T10':return {**S3_CONFIG,'base_rpu':8,'max_rpu':8,'daily_rpu_hours':8,'max_records':32,'max_record_bytes':256}
    raise ValueError('Unknown template')

def catalog(verified=None):
    verified = set(verified if verified is not None else filter(None, os.getenv("VERIFIED_TEMPLATES", "").split(",")))
    return [{"template_id": code, "name": name, "purpose": purpose, "services": services,
             "status": "AWS_VERIFIED" if code in verified and code in IMPLEMENTED else
                       "CODE_READY_UNVERIFIED" if code in IMPLEMENTED else "NOT_IMPLEMENTED",
             "create_enabled": code in verified and code in IMPLEMENTED,
             "configuration": configuration(code) if code in IMPLEMENTED else {},
             'backup_scope':SCOPES[code],
             "operations": ["create", "backup", "restore", "destroy", "audit"] if code in IMPLEMENTED else []}
            for code, name, purpose, services in NAMES]
