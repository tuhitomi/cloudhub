# Trạng thái CloudHub trên GitHub Pages và AWS

Cập nhật ngày 10/10/2026. Website: https://tuhitomi.github.io/cloudhub/. Giao diện được xuất bản bằng GitHub Pages; Cognito, HTTP API, Lambda, DynamoDB và workflow chạy thật trong stack `cloudhub-pages-live`, region `ap-southeast-1`. Stack đã `UPDATE_COMPLETE`. Không cần CloudFront cho giao diện dự phòng.

## Chức năng đã kiểm tra thật

- Đăng nhập Cognito PKCE, đọc danh mục, xem hạn mức và màn hình quản trị qua website công khai.
- T07: hai vòng độc lập tạo → ghi/đọc file → sao lưu → thu hồi nguồn → khôi phục sang môi trường mới → đối chiếu SHA-256 → thu hồi môi trường khôi phục. Cả hai vòng `PASS`; nguồn và môi trường khôi phục đều `DELETED`, báo cáo audit không còn residual/unknown. Gate T07 đã mở cho người dùng.
- Trong hai vòng T07, 12 lệnh dài trả HTTP 202 dưới 2 giây; cao nhất 1.650,48 ms. Kết quả này chưa chứng minh SLA cho toàn bộ mẫu hoặc mọi điều kiện cold start.
- Đăng ký SNS của email quản trị đã `CONFIRMED`. Tác vụ cảnh báo thử từ website đã được AWS nhận và trả Message ID. Chưa kiểm tra thư đến hộp thư người nhận.

T09 đang kiểm tra vòng đời thật sau khi sửa quyền gắn tag API Gateway và CloudWatch Logs. Không mở gate của mẫu đang thử nghiệm từ kết quả lint/mock.

## Phép đo tải và điều kiện còn thiếu

Phép đo đã dùng 20 Cognito subjects riêng biệt, 200 yêu cầu đọc và mức đồng thời 20. Có 192 HTTP 200, 8 lỗi; p95 trên toàn bộ mẫu là 665,55 ms. CloudWatch ghi nhận 8 Lambda throttles trong cùng cửa sổ đo. Do có lỗi, `read_target_met=false` dù p95 nhỏ hơn 2 giây. Quota Lambda thực tế của region là 10 concurrent executions.

Yêu cầu tự động tăng quota lên 30, không tạo Support case, bị AWS từ chối với `IllegalArgumentException`: giá trị yêu cầu phải lớn hơn default quota 1000. Không gửi yêu cầu 1001, không mở Support case mới và không nâng Paid Plan. Cần AWS giải quyết giới hạn tài khoản trước khi có thể nghiệm thu lại chỉ tiêu này.

- T02 còn phụ thuộc việc AWS cho phép tạo CloudFront.
- T06 chưa có image ECR digest được build và duyệt. Docker Engine trên máy hiện trả lỗi khi kiểm tra.
- T10 trả `SubscriptionRequiredException` từ Redshift Serverless trong tài khoản hiện tại. Không tự chuyển Paid Plan.
- Các mẫu khác chỉ được đánh dấu kiểm chứng sau hai vòng thật đạt yêu cầu.
- Chỉ tiêu hết hạn tự nhiên và fallback chưa có kết quả đạt. Không dùng thu hồi thủ công để chứng minh hết hạn tự động.
- Cost Explorer chưa bật; chưa có đối soát hóa đơn cuối cùng. Dự toán không phải chi phí AWS đã đo.

## Bàn giao tài khoản và dữ liệu

Tài khoản quản trị `24020341@vnu.edu.vn` đã có thư mời Cognito, đang ở trạng thái `FORCE_CHANGE_PASSWORD`. Chủ tài khoản tự nhập mật khẩu mới trong lần đăng nhập đầu tiên; mật khẩu không được lưu trong repo hoặc báo cáo.

Tài khoản đo tải được vô hiệu hóa sau kiểm thử. Bản sao thử nghiệm được giữ theo chính sách 7 ngày; chúng vẫn có thể tiêu thụ credits. Môi trường lỗi được giữ khi chưa xác minh sao lưu/thu hồi sạch, không xóa nguồn để làm báo cáo đẹp hơn.

Minh chứng AWS, ảnh và cấu hình riêng nằm ngoài Git. Repository chỉ chứa file văn bản. Kiểm tra local hiện tại: 149 Python tests, 9 DOM tests và các kiểm tra cấu hình đạt. Kết quả local không thay thế minh chứng AWS.
