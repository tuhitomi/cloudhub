# Bản sửa CloudHub 0.4.1 — 10/10/2026

Đã xử lý sáu phát hiện R01–R06 trên nguồn `cf52ef3`. Bản sửa có trong mã và hạ tầng sinh ra; chưa áp dụng lên AWS. Các kiểm thử dùng local mocks và AWS SDK Stubber, không thay thế nghiệm thu AWS thật.

| Phát hiện | Hành vi sau sửa | Bằng chứng kiểm tra |
|---|---|---|
| R01 — bản sao cuối cùng | Tác vụ thu hồi giữ `retirement_lock` trên bản sao. Xóa thủ công, retry xóa backup và dọn hết hạn không vượt được khóa này. Worker kiểm tra lại bản sao trước khi xóa nguồn. Khóa chỉ được gỡ khi audit xác nhận môi trường DELETED; lỗi hoặc đóng tác vụ lỗi vẫn giữ bản sao. | Chặn xóa giữa BACKUP/DELETE, từ chối retry khi bản sao mất/hết hạn, xử lý xung đột cập nhật, giữ khóa qua lỗi/cancel/sweep, gỡ khóa sau audit sạch. UI giải thích và khóa nút xóa, vẫn cho xuất bản sao khả dụng. |
| R02 — dọn backup EBS | Đối chiếu backup job khi thiếu metadata; tìm recovery point bằng cả ba tag owner/environment/recovery và kiểm tra snapshot đúng tài khoản. Chỉ báo xóa khi vault và snapshot đã vắng mặt. Job đang chạy hoặc tài nguyên chưa xác minh được không được coi là đã xóa. | Job hoàn tất sau phản hồi mất, job đang chạy, phân trang vault, từ chối bản sao của người khác, snapshot mồ côi, lỗi quyền và request/response theo SDK thật. |
| R03 — JWT admin cũ | Mọi POST `/environments` với `validation_run:true` kiểm tra quyền admin hiện tại trong Cognito trước tạo môi trường hoặc giữ quota. | Admin bị gỡ quyền nhận 403 dù JWT còn nhóm admin; admin hiện tại được chấp nhận; yêu cầu thường không phát sinh admin lookup. |
| R04 — tài khoản T09 bị khóa | API đọc T09 kiểm tra bản ghi tài khoản trước đọc môi trường và dữ liệu. Quyền GetItem và boundary chỉ mở thêm đúng `U#owner` cùng `E#environment`. | Tài khoản bị khóa nhận 403, không đọc dữ liệu; tài khoản khác không đọc được metadata; policy/boundary giới hạn đúng chủ sở hữu. |
| R05 — thu hồi còn sót | RESIDUAL hoặc unknown kết thúc operation FAILED, giữ quota và bản sao; phát sự kiện `CloudHubCleanupResidual`. Có metric và alarm riêng gửi về AlertTopic SNS. | Cả residual và unknown phát sự kiện; audit lại sạch trả quota/gỡ bảo vệ; cấu hình metric và alarm nối đúng SNS. |
| R06 — quyền máy T01/T05 | Bỏ AmazonSSMManagedInstanceCore. Role chỉ giữ kênh SSM Messages và cập nhật thông tin máy; Explicit Deny cả GetParameter, GetParameters và GetParametersByPath. | Policy không chứa managed policy cũ, allow chỉ gồm năm action cần thiết và Deny ba đường đọc parameter/key pair. |

Kiểm thử bổ sung: [31 ca Python](../tests/test_review_fixes.py) và một ca DOM về bản sao đang bảo vệ nguồn. Bộ kiểm tra cuối cùng đạt **139 Python tests, 9 DOM tests**, không bỏ qua test; compile, JavaScript syntax, OpenAPI và 21 nhóm cfn-lint đều đạt. Kết quả được ghi tại [local-check-results.json](local-check-results.json).

## Các giới hạn còn phải kiểm chứng trên AWS

- CloudFront vẫn phụ thuộc xác minh tài khoản AWS; lần kiểm tra 10/10/2026 thấy CloudShell báo verification in progress và Support case Unassigned.
- IAM đã thu hẹp trong template; cần triển khai rồi kiểm tra SSM hoạt động và thử đọc parameter ngoài phạm vi bị từ chối.
- SNS cần người nhận xác nhận đăng ký và thử nhận email thực tế; thêm alarm không tự tạo người nhận.
- Recovery point hoặc snapshot mồ côi không đủ thông tin để xóa được giữ ở trạng thái chưa xác minh và báo lỗi, cần người vận hành xử lý. Không báo thành công để che tài nguyên còn tồn tại.
- Backup EBS tiếp tục dùng sweeper có khóa để quản lý retention; không thêm AWS Backup lifecycle tự động có thể xóa bản sao đang bảo vệ một lần thu hồi lỗi.
- Chưa có 20 chu kỳ nghiệm thu, đo SLA, kiểm tra hiển thị trực quan/mobile và đối soát chi phí thật.

Các minh chứng trong `review-evidence/` là lịch sử trên nguồn trước sửa. Bộ kiểm tra hiện tại là `tests/` và `scripts/check.py`.
