# Triển khai và vận hành

Nguồn 0.4.0 thêm `AlertEmail` (mặc định trống) và `ActualCostsEnabled` (mặc định false). Chỉ điền email người nhận đã chọn; cần xác nhận subscription và nhận thư kiểm tra. Chỉ bật đối soát sau khi review phí Cost Explorer và kích hoạt tag phân bổ chi phí `cloudhub:environment`; dữ liệu gross có độ trễ. Xem [phạm vi chức năng mới](product-completion.md).

Chạy `npm ci` trước `scripts/check.py` để cài dependency kiểm thử DOM. `configure_frontend.py` cần output `Region`; upload cả `portal.js`. Đăng nhập Cognito lại để lấy scope `aws.cognito.signin.user.admin` cho luồng mật khẩu.

**CloudHub chính chạy hoàn toàn trên AWS tại [CloudHub](https://d3qujq9kfq5gca.cloudfront.net/), stack `cloudhub-pages-live`: S3 riêng tư/CloudFront/OAC, Cognito, API Gateway, Lambda, DynamoDB, Scheduler, Step Functions và CloudFormation.** GitHub chỉ lưu mã và chạy kiểm tra. Pages là phương án lịch sử, không xuất bản bản chính tại đó. Stack gốc `cloudhub-demo` vẫn là dấu vết triển khai cũ; không tự xóa dữ liệu retained hoặc shared control plane.

Nguồn 0.5 thêm chế độ vận hành và chu kỳ riêng, dự toán cộng dồn, tạm dừng T01/T05 và đồng bộ lịch có khóa. Môi trường cũ giữ AUTO_OFF/BACKUP_RETIRE; không chạy migration tự bật gia hạn. Xem [cấu hình, chi phí, migration và kiểm chứng](operating-modes.md) trước khi áp dụng. Chuẩn bị phiếu tài nguyên/dự toán/cách thu hồi, xác nhận account/region/credits/cash/quyền, review changeset không thay thế pool/client/bảng dữ liệu rồi mới thực thi. Giữ đầy đủ GetSchedule/UpdateSchedule và quyền lịch chỉ trên env-* của group dự án; không tăng quota hoặc nâng Paid Plan tự động.

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

Sử dụng `sam deploy --guided --capabilities CAPABILITY_IAM --region ap-southeast-1`, profile đã được duyệt nếu dùng local. Nhập tham số từ config/sam-parameters.json và **review changeset trước thực thi**. Với stack đã có, giữ nguyên tên/physical IDs/cấu hình identity và dữ liệu; không tạo stack thay thế để né lỗi. Không dùng tham số draft còn placeholder. Giá public lưu SSM để tránh vượt giới hạn 4 KiB environment variables Lambda. Bảng giá được đọc lại tối đa mỗi 60 giây; khi lần đọc mới lỗi, không coi cache cũ là đơn giá đã được duyệt. Kiểm lại quotes trước tạo/gia hạn.

Lấy outputs thật bằng CloudFormation describe-stacks vào work-stack-outputs.json, chạy `python scripts/configure_frontend.py work-stack-outputs.json`, sync frontend vào FrontendBucket, invalidation distribution. config.js là metadata công khai; access token chỉ sessionStorage. Tạo 4 user Cognito theo phân công; admin group dành cho người kiểm chứng. Tạo/đổi quyền và credentials cần chủ tài khoản xác nhận tại thao tác, không gửi password/token vào chat hoặc evidence. Đăng ký SNS và xác nhận subscription trước tuyên bố nhận cảnh báo.

## Kiểm chứng theo thứ tự

T01/T02/T03/T04 là mốc bốn mẫu; tiếp tục T05/T07/T06/T09/T08/T10. `aws_lifecycle.py` hỗ trợ đủ 10, dùng account check và hai Cognito user khác nhau. T01/T05 cần OpenSSH client thuộc CIDR được duyệt. POSIX ghi PEM mode 0600; Windows giới hạn DACL thư mục tạm chỉ SID hiện tại trước khi ghi PEM; khóa bị xóa khi xong. SSM dành cho operator, không thay thế chứng minh SSH owner. Không có terminal web.

`run_validation.py` kiểm vòng đời cơ sở, chạy tuần tự danh sách đã duyệt và dừng khi mẫu lỗi để giữ nguồn. Ghi evidence IDs/checksum/audit, không credentials hay signed URL. `review_evidence.py` chỉ đề xuất gate khi có ít nhất 2 vòng thật đã pass và restore/audit/authorization đủ; không tự sửa AWS. Kết quả LOCAL/mock và vòng cơ sở phải tách khỏi bằng chứng chế độ vận hành mới, ghi riêng trong operating-modes.md.

## Sao lưu và lỗi

Mỗi template có scope riêng trong templates.md; restore dựng environment mới. Gia hạn giữ nguồn, chỉ đổi deadline/ledger/lịch bằng transaction và khóa; không gọi create hoặc stop. Tạm dừng T01/T05 giữ instance/root/data EBS, còn phí lưu trữ và vẫn chiếm quota; mở lại cùng nguồn, IP có thể đổi. Thu hồi cần final backup: T01/T05 clean shutdown; RDS app chỉ ghi qua operation lock; EFS remount readonly cả hai máy; T06/S3/DynamoDB/Redshift chỉ ghi dữ liệu được đăng ký qua CloudHub. Backup hoặc checksum lỗi → ERROR_RETAINED/CREATE_FAILED, needs_admin, giữ nguồn/quota và có giới hạn retry; không tiếp tục dừng/xóa nguồn. Retry lỗi mạng cùng yêu cầu dùng lại key cũ. Deadline đã commit nhưng Scheduler lỗi phải giữ schedule_pending và retry operation đồng bộ, không cộng thêm kỳ/chi phí.

RDS có managed secret mới và snapshot restore; T04 app/ASG dựng mới. EFS restore vào filesystem mới đã tag đúng, AWS Backup tạo thư mục restore, driver kiểm/copy /records và đọc hai máy. T10 restore qua CF SnapshotName, rebind CopyRole mới; giữ nguồn khi SQL/checksum/role/usage limit không đúng. Daily Redshift limit 8 RPU-hour có thể deactivate workload; không tăng limit tự động để hoàn tất backup. Plan/quota/SDK paths phải kiểm thật.

## Audit và dọn sau demo

Thu hồi qua owner API: final backup → delete stack → audit. saved IDs gồm generated EC2/root EBS/ENIs theo per-env SG, ALB/ASG/cloudwatch alarms, mount targets, roles/profiles, secrets, tasks/task definitions, API/Lambda/DDB, Redshift usage limits/native snapshots và S3 versions. ID lạ hoặc quyền thiếu → unknown, không trả quota. Service eventual consistency cần chờ và audit lại; không gọi deleted chỉ vì CF stack mất.

Giữ bản sao 7 ngày, chống race restore/expiry; quét phải xác nhận bản sao biến mất trước EXPIRED. Sau môi trường, kiểm riêng retained metadata table/PITR, frontend bucket/distribution, backup vault/bucket, log groups, SSM, SAM packaging, ECR image/repository và network. Xóa shared control plane hoặc dữ liệu retained là thao tác riêng được duyệt. Không xóa tài nguyên có sẵn không xác minh thuộc CloudHub.

Các vòng dài hơn tuổi access token cần refresh: CLOUDHUB_REFRESH_TOKEN/CLOUDHUB_OTHER_REFRESH_TOKEN và CLOUDHUB_COGNITO_DOMAIN/CLOUDHUB_CLIENT_ID (outputs công khai). Token chỉ ở terminal riêng/memory; không gửi qua chat hoặc ghi evidence. Frontend giữ refresh token trong sessionStorage và refresh theo [token endpoint chính thức](https://docs.aws.amazon.com/cognito/latest/developerguide/token-endpoint.html); logout xóa phiên. Auth refresh chưa kiểm chứng AWS.
