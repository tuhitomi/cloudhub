# Metadata khóa cho RDS

Lần T03 đầu trả lỗi KMS key null/not accessible. Hai khóa mặc định trong account đều Enabled. Role CloudFormation thiếu kms:DescribeKey, quyền AWS yêu cầu khi RDS quản lý mật khẩu qua Secrets Manager. [Tài liệu RDS](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/rds-secrets-manager.html).

EnvironmentPolicy2 bổ sung đúng kms:DescribeKey trên key ARN cùng account/region, với ForAnyValue:StringEquals kms:ResourceAliases bằng alias/aws/rds hoặc alias/aws/secretsmanager. AWS-reserved aliases giới hạn metadata của khóa mặc định. [Điều kiện ResourceAliases](https://docs.aws.amazon.com/kms/latest/developerguide/conditions-kms.html#conditions-kms-resource-aliases).

Policy gắn vào EnvironmentCfRole. Boundary của role môi trường sinh viên được giữ nguyên. Policy chỉ đọc metadata; không có Encrypt, Decrypt, GenerateDataKey, CreateGrant hoặc thao tác quản lý khóa. CloudFormation changeset chỉ Add EnvironmentPolicy2, các tài nguyên/code/dữ liệu/người dùng giữ nguyên. Update đã hoàn tất ngày 12/10/2026.

Fixture env-634098b7056f3a2a8f0901e2 chưa từng READY. Đã xác nhận DBInstanceNotFound, không EC2 hoặc snapshot và chỉ có security groups/subnet group riêng. Rollback hoàn tất, mọi resource DELETE_COMPLETE; xóa metadata stack trống rồi audit qua API cho kết quả DELETED, quota_released=true, residual/unknown rỗng. Không tạo bản sao giả cho database không tồn tại.

T03 thử lại đang tạo database; chưa mở gate trước hai vòng dữ liệu/backup/restore/audit thật. Bộ kiểm tra đạt 172 Python tests và 14 DOM tests cùng compile/syntax/OpenAPI/CloudFormation lint. Một test kiểm quyền đúng hai aliases, scope account/region và giữ boundary sinh viên không có KMS.
