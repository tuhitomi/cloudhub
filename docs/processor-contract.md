# Hợp đồng bộ xử lý v0.5

ID dùng chung: environment_id=env-24hex, operation_id/job_id=op-24hex, recovery_point=rp-24hex, owner_id=Cognito sub, deadline epoch UTC seconds. Browser không gửi owner_id, template body, ARN, AMI, role hoặc image tùy ý. Một config cố định mỗi mã; input sai bị từ chối bằng {error:{code,message}}. OpenAPI v0.5 là hợp đồng route/schema. Xem [hai chế độ, ledger và migration](operating-modes.md).

Create/restore/backup/destroy/file write/data commands và renew/change_policy/resume_paused/end/extend trả 202 + operation ID; share link/dự toán/reads trả 200. Service kiểm owner, idempotency fingerprint, CAS lock và quota/budget trong transaction DynamoDB; queued outbox được dispatch lại. Worker trả boolean tiến trình để Standard SF chờ/poll/retry/catch. Không giữ HTTP chờ hạ tầng. Gia hạn do Service dùng chung thực hiện; driver mẫu không dựng lại, dừng hoặc xóa nguồn khi gia hạn.

| Hàm driver | Quy tắc |
|---|---|
| create / created | deterministic CF stack, owner tags, client token; discover nguồn/tài nguyên tạo dở |
| ready | service status + data mount/app health; restore checksum/data verification trước READY |
| backup | đúng phạm vi template; nguồn đóng ghi; job/token/name chống lặp; AVAILABLE sau xác minh |
| resume | mở ghi/khởi động lại đúng nguồn; kiểm readiness trước READY |
| write_file / use_data | owned READY, deadline còn, durable lock, input/quota nhỏ; operation result riêng owner |
| delete | final backup trước source deletion; chỉ scoped CF stack/data versions/task definitions/usage limit |
| inventory / audit | saved IDs + scoped generated IDs + native retained backups; permission lỗi/unsupported giữ unknown |
| schedule / cancel_schedule | lịch ổn định env-ID-lifecycle, one-time UTC/OFF/NONE; Get trước Update, giữ cấu hình đầy đủ; callback kiểm deadline/revision hiện hành; hủy sau tạm dừng/thu hồi |
| pause / pause_audit | chỉ T01/T05; dừng cùng instance, giữ root/data EBS và stack; báo retained/residual/unknown/billable; không trả quota |
| expire_backup | EXPIRING fence và restore lock; kiểm owner/environment/recovery tags và xác nhận xóa |

States: QUEUED→RUNNING→SUCCEEDED/FAILED; PROVISIONING→READY→BACKING_UP→READY hoặc RETIRING→DELETED/RESIDUAL; PAUSING→PAUSED→RESTARTING→READY. Backup cuối lỗi giữ source/needs_admin/quota, tối đa hai retry tự động sau lần đầu. Chỉ quota active trả khi audit DELETED sạch; reserved estimate người dùng/toàn hệ thống và ledger môi trường vẫn được giữ, không tự hoàn lại để tạo vòng tái tạo vượt ngân sách. Các ledger là tổng dự toán đã cam kết, không phải hóa đơn thực tế hay bảo đảm chi phí bản sao/log sau thu hồi.

Scope và reuse từng mẫu xem templates.md. T07 signed link pin VersionId, tối đa 900 giây/hạn phiên; người có link có thể tải trong thời gian hiệu lực, link cũ không được restore. T06 chỉ filesystem S3 đăng ký được bảo vệ. T09 logical backup giữ user records, dựng lại table/API/Lambda; không restore operation journal cũ. T10 snapshot warehouse + S3, query checksum riêng.

Thay đổi kích thước tài nguyên chưa hỗ trợ, trả INVALID_INPUT thay vì sửa tùy ý. Chu kỳ vận hành/gia hạn 5–480 phút; khoảng xem dự toán 1–8 giờ độc lập deadline. Legacy /extend nhận 1–4 giờ và dùng cùng thao tác gia hạn, có ledger/quota/khóa. Registry gate server-side giữ mọi mẫu unverified cho đến 2 vòng AWS thật. Lint/test không chứng minh IAM thực thi hoặc AWS service support; kiểm chứng vòng đời cũ không tự chứng minh mọi tổ hợp chế độ mới.
