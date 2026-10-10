# Hợp đồng bộ xử lý v0.3

ID dùng chung: environment_id=env-24hex, operation_id/job_id=op-24hex, recovery_point=rp-24hex, owner_id=Cognito sub, deadline epoch UTC seconds. Browser không gửi owner_id, template body, ARN, AMI, role hoặc image tùy ý. Một config cố định mỗi mã; input sai bị từ chối bằng {error:{code,message}}. OpenAPI v0.3 là hợp đồng route/schema.

Create/restore/backup/destroy/file write/data commands trả 202 + operation ID; share link/gia hạn/reads trả 200. Service kiểm owner, idempotency fingerprint, CAS lock và quota/budget trong transaction DynamoDB; queued outbox được dispatch lại. Worker trả boolean tiến trình để Standard SF chờ/poll/retry/catch. Không giữ HTTP chờ hạ tầng.

| Hàm driver | Quy tắc |
|---|---|
| create / created | deterministic CF stack, owner tags, client token; discover nguồn/tài nguyên tạo dở |
| ready | service status + data mount/app health; restore checksum/data verification trước READY |
| backup | đúng phạm vi template; nguồn đóng ghi; job/token/name chống lặp; AVAILABLE sau xác minh |
| resume | mở ghi/khởi động lại đúng nguồn; kiểm readiness trước READY |
| write_file / use_data | owned READY, deadline còn, durable lock, input/quota nhỏ; operation result riêng owner |
| delete | final backup trước source deletion; chỉ scoped CF stack/data versions/task definitions/usage limit |
| inventory / audit | saved IDs + scoped generated IDs + native retained backups; permission lỗi/unsupported giữ unknown |
| schedule | one-time UTC OFF flexible window, target đọc lại deadline; gia hạn tạo schedule mới |
| expire_backup | EXPIRING fence và restore lock; kiểm owner/environment/recovery tags và xác nhận xóa |

States: QUEUED→RUNNING→SUCCEEDED/FAILED; PROVISIONING→READY→BACKING_UP→READY hoặc RETIRING→DELETED/RESIDUAL. Backup cuối lỗi giữ source/needs_admin/quota. Quota active trả khi audit sạch; reserved cost không tự hoàn lại để ngăn vòng tái tạo vượt budget.

Scope và reuse từng mẫu xem templates.md. T07 signed link pin VersionId, tối đa 900 giây/hạn phiên; người có link có thể tải trong thời gian hiệu lực, link cũ không được restore. T06 chỉ filesystem S3 đăng ký được bảo vệ. T09 logical backup giữ user records, dựng lại table/API/Lambda; không restore operation journal cũ. T10 snapshot warehouse + S3, query checksum riêng.

Thay đổi tài nguyên chưa hỗ trợ, trả INVALID_INPUT thay vì sửa tùy ý; gia hạn 1–4 giờ, tổng tối đa 8 giờ. Registry gate server-side giữ mọi mẫu unverified cho đến 2 vòng AWS thật. Lint/test không chứng minh IAM thực thi hoặc AWS service support.
