# Kiểm chứng và trình diễn

**Hiện có 71 local/mock tests; 0 vòng AWS; chưa đo SLA.** Ảnh AWS Console/CloudShell là bằng chứng account blocker, không phải CloudHub đã chạy. CI chưa có GitHub run.

## Hai vòng mỗi mẫu sau deploy được duyệt

Đăng nhập Cognito PKCE lấy token vào terminal riêng: CLOUDHUB_ACCESS_TOKEN của owner/admin và CLOUDHUB_OTHER_ACCESS_TOKEN của người khác. Không lưu/chụp token. T01/T05 harness cần Linux/OpenSSH và CIDR cho client đó. Chạy mốc T01–T04 trước, sau đó các mẫu còn lại, bằng aws_lifecycle.py hoặc danh sách tuần tự của run_validation.py:

```sh
python scripts/aws_lifecycle.py --api <HTTPS_API> --config config/local.json --template T01 --cycles 2 --out evidence/T01.json --approved-create-resources
python scripts/run_validation.py --api <HTTPS_API> --config config/local.json --out-dir evidence --approved-create-resources
python scripts/review_evidence.py --config config/local.json --evidence-dir evidence --out evidence/gate-review.json
```

Mỗi vòng ghi actual operation IDs/resources/timestamps/latencies: create/readiness → check không token 401 và khác owner 404 → ghi UUID → truy cập/đọc lại → manual backup → final backup/destroy/source audit → restore sang ID mới → dữ liệu/checksum + restore_verified → final backup/destroy/audit restore. T01/T05 SSM fixture kèm **SSH thực tế**; T02 public HTTPS; T03/T04 HTTP health và PostgreSQL records; T06 container /files; T07 signed download; T08 hai mounted nodes; T09 JWT data API; T10 S3 COPY và query warehouse. Một lỗi làm dừng chuỗi, giữ nguồn.

PASS lifecycle không là chứng minh SLA. Backups retained 7 ngày vẫn billable; đợi EXPIRED và audit lại mới kết thúc dọn account. Không tăng Passed cycles khi chỉ chạy mock.

## Đo riêng

`benchmark.py --api ... --out evidence/read-performance.json --requests 200`: 20 concurrent workers, CLOUDHUB_BENCH_TOKENS là JSON array token của 20 **sub khác nhau** (issuer/sub được đếm, API xác thực). Một token chỉ đo 20 client cùng user, không đạt tiêu chí 20 user. p95 tất cả reads <2000 ms và responses200 mới đạt. HTTP202 latency được ghi riêng từng long command trong lifecycle harness.

`measure_expiry.py --api ... --environment <READY_ID> --out evidence/expiry.json`: chờ deadline tự nhiên, không manual destroy. So expiry_triggered_at với deadline, mục tiêu <120 giây; thời gian backup/delete hoàn tất ghi riêng. Xem Scheduler invocation/DLQ/CloudWatch để chứng minh nguồn kích hoạt; cấu hình fallback rate(5 minutes) cần log invoke thực tế. Lỗi outbox có thể tới quét 5 phút, chưa cam kết 2 phút trong sự cố.

Kiểm fault riêng: quyền chéo/quota đồng thời/idempotency, gia hạn sát deadline, nguồn tạo dở/lost responses, Backup/Snapshot/SSM/DataAPI thất bại, retention race, restore checksum sai và residual unknown. Chỉ gây lỗi trong env được duyệt; không xóa backup/nguồn chung để tạo demo.

## Demo 8–12 phút

Đăng nhập và danh mục đúng mục đích → quote có giá/giả định/credit không vô hạn → tạo 202/poll → READY và deadline bắt đầu → owner truy cập/ghi dữ liệu → backup scope/gián đoạn/retention → destroy/audit retained-billable → restore ID mới và checksum thật → chỉ ra CloudWatch/SFN/jobs/evidence; kết thúc bằng tài nguyên còn có phí và kế hoạch dọn. Chỉ mở mẫu đã kiểm chứng. Không chụp key/password/token/signed URL.

Các vòng dài hơn tuổi access token cần refresh: CLOUDHUB_REFRESH_TOKEN/CLOUDHUB_OTHER_REFRESH_TOKEN và CLOUDHUB_COGNITO_DOMAIN/CLOUDHUB_CLIENT_ID (outputs công khai). Token chỉ ở terminal riêng/memory; không gửi qua chat hoặc ghi evidence. Frontend giữ refresh token trong sessionStorage và refresh theo [token endpoint chính thức](https://docs.aws.amazon.com/cognito/latest/developerguide/token-endpoint.html); logout xóa phiên. Auth refresh chưa kiểm chứng AWS.
