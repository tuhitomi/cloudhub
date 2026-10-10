# Triển khai và vận hành

Nguồn 0.4.0 thêm `AlertEmail` (mặc định trống) và `ActualCostsEnabled` (mặc định false). Chỉ điền email người nhận đã chọn; cần xác nhận subscription và nhận thư kiểm tra. Chỉ bật đối soát sau khi review phí Cost Explorer và kích hoạt tag phân bổ chi phí `cloudhub:environment`; dữ liệu gross có độ trễ. Xem [phạm vi chức năng mới](product-completion.md).

Chạy `npm ci` trước `scripts/check.py` để cài dependency kiểm thử DOM. `configure_frontend.py` cần output `Region`; upload cả `portal.js`. Đăng nhập Cognito lại để lấy scope `aws.cognito.signin.user.admin` cho luồng mật khẩu.

**Backend dự phòng `cloudhub-pages-live` đã triển khai và cập nhật thành công; giao diện tại https://tuhitomi.github.io/cloudhub/.** Xem [cách kết nối Pages](github-pages.md). Stack CloudFront gốc `cloudhub-demo` vẫn ROLLBACK_COMPLETE; không tạo lại/xóa dữ liệu retained chưa kiểm kê. Các bước CloudFront dưới đây dành cho phương án D01 gốc, không phải điều kiện chặn backend dự phòng.

## Trước lệnh tạo AWS

1. Khi xác minh hoàn tất, dùng CloudShell hoặc AWS CLI/SAM trên máy qua SSO/profile có quyền phù hợp. Chạy STS để kiểm account 133130236086 và region ap-southeast-1. Không tạo root access key.
2. Chọn network đã được duyệt hoặc review `infra/network.json` rồi tạo shared network riêng. Kiểm VPC DNS, public subnet/IGW, hai AZ và DB subnet pair. Điền IDs thật vào bản local của config example.
3. Chọn AL2023 x86_64 AMI trong region; tìm PostgreSQL version còn orderable cho db.t3.micro/gp3; điền SSH CIDR /24 hoặc hẹp hơn được duyệt. PostgreSQL 16.3 trong test chỉ là fixture lint, không phải phiên bản đề nghị chạy năm 2026.
4. Với T06, review `infra/container/Dockerfile` và app contract, build từ **root source** (`docker build -f infra/container/Dockerfile ... .`), quét/review image, đẩy ECR được duyệt, lấy digest; không dùng mutable latest hoặc image tùy ý. Registry/repository/image là tài nguyên chung cần chi phí và cleanup riêng.
5. Chạy `python scripts/preflight.py --config config/local.json --out evidence/preflight.json`. Script chỉ đọc STS, Free Plan/credits, network/AMI/RDS/ECR, quyền đọc Redshift và quotas. Đọc thành công không chứng minh có quyền tạo/restore. Không đổi `preflight_passed=true` nếu chưa xem kết quả đạt; không tự nâng Paid Plan nếu T10 bị account từ chối.
6. Review giá chính thức, ngân sách/credits, cash=0, phạm vi IAM và resource/cleanup list. Giữ `verified_templates=[]`, chỉ admin validation có T01–T10. Khi được xác nhận đủ, đặt acknowledged=true và prices approved=true rồi chạy prepare_deployment không có --draft. Cấu hình còn thiếu bị từ chối.

```sh
python scripts/check.py
python scripts/prepare_deployment.py --config config/local.json --prices config/prices.local.json
sam validate --lint --template infra/control-plane.json
sam build --template infra/control-plane.json
```

## Deploy sau review cụ thể

Sử dụng `sam deploy --guided --capabilities CAPABILITY_IAM --region ap-southeast-1`, profile đã được duyệt nếu dùng local. Nhập tham số từ config/sam-parameters.json và **review changeset trước thực thi**. Chọn stack name ngắn/chữ thường như cloudhub-demo. Không dùng tham số draft còn placeholder. Giá public lưu SSM để tránh vượt giới hạn 4 KiB environment variables Lambda. Bảng giá được cache trong worker warm runtime; thay giá cần redeploy/refresh worker và kiểm lại quotes.

Lấy outputs thật bằng CloudFormation describe-stacks vào work-stack-outputs.json, chạy `python scripts/configure_frontend.py work-stack-outputs.json`, sync frontend vào FrontendBucket, invalidation distribution. config.js là metadata công khai; access token chỉ sessionStorage. Tạo 4 user Cognito theo phân công; admin group dành cho người kiểm chứng. Tạo/đổi quyền và credentials cần chủ tài khoản xác nhận tại thao tác, không gửi password/token vào chat hoặc evidence. Đăng ký SNS và xác nhận subscription trước tuyên bố nhận cảnh báo.

## Kiểm chứng theo thứ tự

T01/T02/T03/T04 là mốc bốn mẫu; tiếp tục T05/T07/T06/T09/T08/T10. `aws_lifecycle.py` hỗ trợ đủ 10, dùng account check và hai Cognito user khác nhau. T01/T05 cần OpenSSH client thuộc CIDR được duyệt. POSIX ghi PEM mode 0600; Windows giới hạn DACL thư mục tạm chỉ SID hiện tại trước khi ghi PEM; khóa bị xóa khi xong. SSM dành cho operator, không thay thế chứng minh SSH owner. Không có terminal web.

`run_validation.py` chạy tuần tự danh sách đã duyệt và dừng khi mẫu lỗi để giữ nguồn. Ghi evidence IDs/checksum/audit, không credentials hay signed URL. `review_evidence.py` chỉ đề xuất gate khi có ít nhất 2 vòng thật đã pass và restore/audit/authorization đủ; không tự sửa AWS. Chưa có bằng chứng thật nào trong gói hiện tại.

## Sao lưu và lỗi

Mỗi template có scope riêng trong templates.md; restore dựng environment mới. Không đổi kích thước tài nguyên; chỉ gia hạn thời lượng có quota/lock/deadline fence. T01/T05 clean shutdown; RDS app chỉ ghi qua operation lock; EFS remount readonly cả hai máy; T06/S3/DynamoDB/Redshift chỉ ghi dữ liệu được đăng ký qua CloudHub. Backup hoặc checksum lỗi → ERROR_RETAINED/CREATE_FAILED, needs_admin, giữ nguồn/quota. Sửa nguyên nhân và retry có giới hạn qua operation key mới, không bỏ qua final backup. Retry lỗi mạng cùng yêu cầu dùng lại key cũ.

RDS có managed secret mới và snapshot restore; T04 app/ASG dựng mới. EFS restore vào filesystem mới đã tag đúng, AWS Backup tạo thư mục restore, driver kiểm/copy /records và đọc hai máy. T10 restore qua CF SnapshotName, rebind CopyRole mới; giữ nguồn khi SQL/checksum/role/usage limit không đúng. Daily Redshift limit 8 RPU-hour có thể deactivate workload; không tăng limit tự động để hoàn tất backup. Plan/quota/SDK paths phải kiểm thật.

## Audit và dọn sau demo

Thu hồi qua owner API: final backup → delete stack → audit. saved IDs gồm generated EC2/root EBS/ENIs theo per-env SG, ALB/ASG/cloudwatch alarms, mount targets, roles/profiles, secrets, tasks/task definitions, API/Lambda/DDB, Redshift usage limits/native snapshots và S3 versions. ID lạ hoặc quyền thiếu → unknown, không trả quota. Service eventual consistency cần chờ và audit lại; không gọi deleted chỉ vì CF stack mất.

Giữ bản sao 7 ngày, chống race restore/expiry; quét phải xác nhận bản sao biến mất trước EXPIRED. Sau môi trường, kiểm riêng retained metadata table/PITR, frontend bucket/distribution, backup vault/bucket, log groups, SSM, SAM packaging, ECR image/repository và network. Xóa shared control plane hoặc dữ liệu retained là thao tác riêng được duyệt. Không xóa tài nguyên có sẵn không xác minh thuộc CloudHub.

Các vòng dài hơn tuổi access token cần refresh: CLOUDHUB_REFRESH_TOKEN/CLOUDHUB_OTHER_REFRESH_TOKEN và CLOUDHUB_COGNITO_DOMAIN/CLOUDHUB_CLIENT_ID (outputs công khai). Token chỉ ở terminal riêng/memory; không gửi qua chat hoặc ghi evidence. Frontend giữ refresh token trong sessionStorage và refresh theo [token endpoint chính thức](https://docs.aws.amazon.com/cognito/latest/developerguide/token-endpoint.html); logout xóa phiên. Auth refresh chưa kiểm chứng AWS.
