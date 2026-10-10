# 10 mẫu và kết quả kiểm chứng AWS

| Mã | Tài nguyên chuẩn / reuse | Bản sao bảo vệ | Giá 1h gross dự toán USD | AWS cycles |
|---|---|---|---:|---:|
| T01 | 1 t3.micro standard; root16/data8 gp3; SSH CIDR/SSM; dùng network chung | EBS /data sau dừng máy; không gồm root, RAM hoặc dữ liệu ngoài /data | 0.497 | 0/2 |
| T02 | Private versioned S3 + CloudFront/OAC/policy; 32 file ×8 KiB | File S3 hiện hành, checksum SHA-256; không gồm lịch sử phiên bản hay cấu hình CloudFront | 0.331 | 0/2 |
| T03 | 1 t3.micro root16 + PostgreSQL db.t3.micro 20gp3 private + managed secret | PostgreSQL: snapshot RDS và checksum bản ghi minh họa; không gồm root EC2 | 1.037 | 0/2 |
| T04 | Reuse T03 app/RDS; ALB2AZ + ASG min1/max2 t3.micro + target tracking | PostgreSQL: snapshot RDS và checksum; ALB/ASG/EC2 được dựng mới | 1.228 | 0/2 |
| T05 | Reuse T01; nginx phục vụ nội dung /data | EBS /data và nội dung web trong /data; không gồm root/RAM | 0.497 | 0/2 |
| T06 | 1 Fargate task .25vCPU/.5GiB + ALB + private versioned S3; own ECR digest | S3 được đăng ký làm dữ liệu bền vững; không gồm filesystem tạm của container, RAM hay image | 0.565 | 0/2 |
| T07 | Reuse T02 S3; bỏ CloudFront; signed URL pin VersionId, <=15 phút | File S3 hiện hành, checksum SHA-256; không giữ hiệu lực liên kết chia sẻ cũ | 0.361 | 2/2 PASS |
| T08 | Encrypted EFS +2mount targets +2t3.micro root16, SSM; NFS chỉ nodeSG | EFS sau khóa ghi cả hai máy; checksum /records; không gồm root EC2 | 0.471 | 0/2 |
| T09 | PAY_PER_REQUEST DDB + Lambda128MiB/5s + HTTPApi JWT owner, throttle5/s burst10 | Bản ghi người dùng DynamoDB, đọc nhất quán và SHA-256; không gồm nhật ký idempotency của môi trường cũ | 0.411 | 0/2 |
| T10 | Redshift namespace/WG private 8RPU max8 + S3 dataset/CopyRole/managed secret; daily8RPUh deactivate | Snapshot namespace Redshift và file S3 hiện hành; checksum dữ liệu truy vấn, không gồm kết quả truy vấn tạm | 11.175 | 0/2 |

Mỗi mẫu chỉ có config trong catalog.configuration; không nhận custom IaC/image/role. T03/T04/T08/T09 max32 records ×256 byte UTF-8; T10 CSV minh họa nạp lại thay bảng records. Chưa hỗ trợ resize tài nguyên hoặc arbitrary workload. T06 filesystem container tạm không được sao lưu.

Native RDS/Redshift snapshots hoặc AWS Backup EFS có thể chứa dữ liệu nội bộ nhiều hơn fixture checksum; checksum kiểm phần demo có hợp đồng. EFS full filesystem có thể chứa thư mục staging từ restore trước. S3 chỉ current files, không restore version history hoặc hiệu lực signed links. T09 không giữ operation journals cũ.

T07 đã mở gate sau hai vòng AWS thật. Các mẫu còn lại chưa đủ minh chứng để mở; xem [trạng thái triển khai](pages-live-status.md). T10 cần account/region/plan thực sự cho phép Redshift Serverless và restore; đọc API không đủ chứng minh quyền create. Không tự nâng Paid Plan.
