# Báo cáo tổng hợp CloudHub

Trạng thái hiện hành ngày 12/10/2026: [website AWS](https://d3qujq9kfq5gca.cloudfront.net/) vận hành bằng S3 riêng tư + CloudFront/OAC theo kế hoạch gốc, giữ backend/dữ liệu/người dùng hiện có. AWS Support đã xác minh tài khoản. Đăng nhập API Cognito 0.4.2 đã được người dùng duyệt, triển khai và kiểm tra giao diện thật; hai tài khoản thử riêng xác nhận quyền quản trị/owner. T01, T02, T07 và T09 đạt 2/2 vòng AWS và mở gate sau review. T03–T04 đang kiểm lần lượt; T06 có digest own-account quét sạch, chưa đủ vòng Fargate. T10 vẫn bị Free Plan từ chối.

Kiểm tra local: 172 Python + 14 DOM tests cùng compile/syntax/OpenAPI/cfn-lint đạt. Phép đo tải cũ 20 chủ thể/200 yêu cầu có 31 lỗi throttling. Người dùng đã chốt giữ quota 10 tác vụ Lambda đồng thời cho demo, không xin tăng quota hay gửi Support; mục tiêu 20 người không còn là điều kiện nghiệm thu demo. Timer T07 kích hoạt sau 6 giây được xác minh từ timestamp AWS/CloudTrail; fallback riêng kích hoạt sau 92 giây, backup AVAILABLE và audit nguồn sạch. T01 có hai lệnh tạo khoảng 10,8 giây nên chưa đạt chỉ tiêu phản hồi dưới 2 giây. Xem [trạng thái thật hiện hành](pages-live-status.md) và [thay đổi đăng nhập đã áp dụng](cognito-api-signin-review.md). Các phần dưới là lịch sử, số vòng/URL/blocker tại từng thời điểm.

## Lịch sử ngày 9–10/10/2026

Cập nhật triển khai dự phòng 10/10/2026: [website GitHub Pages](https://tuhitomi.github.io/cloudhub/) đã kết nối stack AWS `cloudhub-pages-live` (UPDATE_COMPLETE) tại ap-southeast-1. Đăng nhập Cognito, danh mục, hạn mức và giao diện quản trị đã được kiểm tra trên website thật. Tài khoản quản trị đã có thư mời Cognito. Kiểm tra local hiện tại: 149 Python và 9 DOM tests đạt; CI và xuất bản Pages đã đạt ở commit kết nối backend. SNS đã có đăng ký email CONFIRMED; cảnh báo thử từ website trả Message ID, nhưng chưa xác minh người nhận đã nhận thư. Vòng đời AWS đang được kiểm tra riêng và gate vẫn chỉ mở theo bằng chứng; chưa nghiệm thu đủ 20 vòng/SLA. Xem [triển khai Pages](github-pages.md) và README cho trạng thái hiện hành.

T07 đã đạt hai vòng AWS thật và mở gate. Phép đo 20 người dùng có 192/200 yêu cầu thành công, 8 Lambda throttles nên chưa đạt chỉ tiêu tải. [Trạng thái triển khai thật](pages-live-status.md) ghi chi tiết bằng chứng và các điều kiện còn thiếu.

## Lịch sử trước triển khai dự phòng

Các trạng thái rollback, không có URL và số 0/20 bên dưới là hồ sơ của lần triển khai gốc, không phản ánh trạng thái website dự phòng hiện tại.

Cập nhật ngày 10/10/2026: nguồn **0.4.1** sửa sáu phát hiện R01–R06; xem [chi tiết](security-fixes.md) và [kết quả kiểm tra mới nhất](local-check-results.json). Các số kiểm thử ở phần lịch sử bên dưới thuộc các phiên bản trước. Bản sửa chưa triển khai AWS; tài khoản vẫn đang xác minh trong lần kiểm tra 10/10, stack `cloudhub-demo` ROLLBACK_COMPLETE.

Cập nhật nguồn 0.4.0: xem [báo cáo 10 điểm bổ sung](product-completion.md). Kiểm tra hiện tại: **108 Python local/mock/SDK tests + 8 DOM tests**, compile/syntax/OpenAPI/cfn-lint đều đạt. Bản mới chưa triển khai, AWS vẫn 0/20 vòng. Số 71 tests và trạng thái AWS bên dưới là kết quả giai đoạn trước.

**Có mã nguồn cho cả 10 mẫu; đã triển khai mạng AWS nhưng control plane bị AWS chặn xác minh CloudFront.** Đã hoàn tất các phần làm độc lập tại workspace: frontend/backend/workflow/IaC, processor T01–T10, OpenAPI, bộ kiểm tra, CI, review dự toán, tài liệu vận hành và minh chứng. **AWS thật: 0/20 vòng, chưa đo SLA.** Mọi mẫu vẫn khóa với người dùng thường.

## Kết quả đã thực hiện

- F00–F02: giao diện tiếng Việt theo mục đích, Cognito PKCE/state/access token/scope, owner lấy từ sub; catalog chuẩn và gate server-side. Có quản lý file, signed download T07, record operations, truy vấn T10, polling và admin attention.
- F03–F05: đơn giá có nguồn/thời điểm/assumptions; quota user/global và reserved budget trong transaction; idempotency/fingerprint/CAS locks/outbox; Standard SF wait/retry/catch/reconcile. Deadline từ READY, gia hạn có fence, expiry đọc lại trạng thái.
- F06: bản sao riêng từng mẫu, source shutdown/freeze, restore môi trường mới và checksum dữ liệu. Final backup lỗi giữ source/quota/needs_admin. Recovery point lock chống race retention.
- F07–F08: log/metric filter/alarms/dashboard/DLQ và SNS chưa subscription; inventory/audit exact IDs bao gồm generated ENIs/EBS, ASG alarms/tasks, per-env secrets và native snapshots. Báo deleted/retained/residual/unknown/billable; không trả quota khi còn unknown.
- D01–D13: SAM control plane, Standard workflow, shared optional network không NAT, IAM boundaries/roles, AWS SDK drivers. Giá lưu SSM thay vì Lambda environment để tránh vượt 4 KiB.

## Các mẫu

| Mã | Đã có mã | AWS thật |
|---|---|---:|
| T01 | Linux/SSH/SSM/EBS/Backup | 0/2 |
| T02 | S3 private/CloudFront/file upload/manifest | 0/2 |
| T03 | EC2 app/PostgreSQL private/managed secret/snapshot | 0/2 |
| T04 | Reuse T03 + ALB/ASG/target tracking/restore | 0/2 |
| T05 | Reuse T01 + nginx | 0/2 |
| T06 | Fargate approved digest + S3 data/ALB | 0/2 |
| T07 | Reuse S3 T02 + signed version-pinned links | 0/2 |
| T08 | 2 Linux/EFS/freeze/AWS Backup restore | 0/2 |
| T09 | JWT owner API/Lambda/DDB logical archive | 0/2 |
| T10 | Redshift private/S3 COPY/snapshot/role rebind/RPU usage limit | 0/2 |

Chi tiết tài nguyên/cấu hình/phạm vi ở templates.md. Có source không đồng nghĩa AWS_VERIFIED. T06 image chưa build/push/review thực tế; T10 cần kiểm account plan/quota/service access. Runtime/API đã tra cứu tài liệu chính thức, CF lint và SDK Stubber không thay thế việc thử thật.

## Kiểm tra đã chạy

`generate_infra.py`, `generate_network.py`, `generate_openapi.py`, `check.py`, `prepare_deployment.py --draft`, kiểm CLI từ chối draft chưa duyệt và kiểm gói ZIP. Kết quả trong local-check-results.json: **71 tests local/mock**, SDK shape/stub và HTTP container test; Python compile, node --check, OpenAPI validation và cfn-lint control plane/shared network + các nhánh create/restore. Không có skipped test được tính pass. Đã đóng gói Python/Lambda và triển khai qua CloudFormation changeset thật; GitHub CI và vòng đời AWS chưa chạy. Harness SSH đã hỗ trợ Windows OpenSSH với thư mục PEM chỉ cấp ACL cho SID hiện tại, ngoài POSIX mode 0600.

Browser preview localhost 127.0.0.1:8876 không tới được server (ERR_CONNECTION_TIMED_OUT). Chưa xác minh bố cục/luồng UI qua browser hoặc trên AWS; không cung cấp ảnh UI giả. Local HTTP container test dùng socket thật và fake S3 được ghi rõ là local.

## AWS và ngân sách

Đã đăng nhập AWS CLI v2 bằng phiên root tạm thời qua `aws login --remote`, theo xác nhận riêng của người dùng; không tạo root access key. STS xác nhận account **133130236086**. Free Tier API xác nhận **FREE / ACTIVE**, **183,66 USD credits**, hết hạn **07/03/2027**. Đã duyệt **120 USD credits / 0 USD tiền mặt**; không nâng Paid Plan hoặc support plan.

Preflight đọc thực tế đạt cả 5 nhóm: Free Plan/credits, network, AL2023 x86_64, PostgreSQL **16.15** và service quotas. AMI Singapore: `ami-01a395a37625fb28c`. Gói Lambda chứa SDK đã kiểm CRC/import và upload vào S3 private.

`cloudhub-network` **CREATE_COMPLETE**: VPC `vpc-0a6b1df144299b7ce`, CIDR `10.42.0.0/16`, 2 public subnet + 2 DB subnet/2 AZ, IGW và route tables; không NAT. Changeset mạng có 14 additions; control plane có 65 additions sau SAM transform.

`cloudhub-demo` đã thực sự chạy tạo tài nguyên nhưng **ROLLBACK_COMPLETE**. CloudFront trả HTTP **403 / AccessDenied**, yêu cầu AWS xác minh account trước khi cho tạo distribution. Request ID: `ffea93d7-4822-4c7a-b9c2-72c074de9450`. Không có website/API dùng được, chưa tạo quản trị Cognito, chưa chạy được vòng đời AWS. Không đổi kiến trúc để vượt điểm chặn. [Ảnh lỗi AWS thật](screenshots/aws-cloudfront-verification-blocked.png).

AWS Support case **179155110800156** đã gửi, trạng thái **Unassigned**.

Sau rollback còn mạng dùng chung, 3 S3 buckets (2 bucket trống; bucket deploy chứa **16.774.648 bytes**), một DynamoDB table trống **PAY_PER_REQUEST / PITR DISABLED**, và Backup Vault **0 recovery points** do `DeletionPolicy: Retain`. Kiểm kê CloudHub cho thấy 0 EC2, 0 Lambda, 0 Cognito pool và 0 IAM roles mang prefix stack còn hoạt động. Các bucket có public-access block và encryption. Tài nguyên giữ lại không đồng nghĩa deployment thành công; phí lưu S3/request có thể trừ credits và dữ liệu billing có độ trễ. Không có số chi phí AWS cuối cùng đã đo. Không xóa/sửa tài nguyên có trước của tài khoản.

Đơn giá có nguồn/thời điểm, 33 units được review. Dự toán thận trọng cả 20 vòng: **99,438 USD gross + 10 USD shared allowance**, trong hạn mức **120 USD credits**, tiền mặt **0 USD**. T10 chiếm 67,050 USD. Quota ứng dụng không phải hard billing cap.

## Báo cáo gốc và phân công

Đã đọc báo cáo DOCX gốc và đối chiếu đủ A01–A25, giữ file gốc không sửa. **880 giờ gốc (800+80)** so với yêu cầu **560 giờ (500+60)**, chênh **320 giờ**. Giữ nguyên số gốc; chưa có giờ người thực tế để chứng minh lịch 10 tuần hay mốc bốn mẫu AWS tuần 4–5. Không bịa effort hoặc giảm số trên giấy.

| Thành viên | Trách nhiệm theo yêu cầu |
|---|---|
| TV1 | Review UI/UX, polling, auth states, tích hợp và demo |
| TV2 | API/OpenAPI/JWT, quota/budget/data, 20-user benchmark |
| TV3 | Review IaC/IAM/network, T01–T06/RDS/ALB/ASG/image và triển khai |
| TV4 | Expiry/faults, backup/restore/audit/retention, EFS/Redshift |

Mã A/tên/giờ/phụ trách gốc trong report-traceability.md và config/report-items.json. CI có cấu hình, Git đã init nhưng chưa commit/remote/PR, không bịa lịch sử.

## Điểm chặn và bước còn lại

AWS phải xác minh và mở tạo CloudFront. Root/profile, region, ngân sách, đơn giá, network/AMI/PostgreSQL và preflight đã xác nhận; không hỏi duyệt lại cùng phạm vi khi tiếp tục. T06 còn thiếu Docker build/ECR digest; WSL bị access denied và Docker Desktop chưa chạy trong môi trường này.

Trước khi thử lại: kiểm kết quả Support, kiểm lại Free Plan/credits, AMI và preflight. Stack ROLLBACK_COMPLETE không update được; kiểm kê bốn resource Retain, quyết định import/reuse hoặc dọn tài nguyên trống qua bước review cụ thể để tránh xung đột Backup Vault tên cố định. Không xóa dữ liệu quản lý/bản sao thật. Giữ network và artifact để tiếp tục.

Control plane thành công → cấu hình/upload frontend và tạo quản trị Cognito theo email đã cung cấp → hai vòng T01–T04 → sáu mẫu còn lại → benchmark 20 chủ thể Cognito và đo expiry/fallback → bàn giao/retention/audit. Gate `verified_templates=[]` được giữ nguyên; local tests không thay thế AWS lifecycle. **Mọi mẫu vẫn 0/2; tổng 0/20; SLA chưa đo.**
