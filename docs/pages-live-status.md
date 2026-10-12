# Trạng thái CloudHub trên AWS

Cập nhật ngày 12/10/2026. Website chính: https://d3qujq9kfq5gca.cloudfront.net/. Đã trở lại D01 theo kế hoạch gốc: S3 riêng tư + CloudFront/OAC. Cognito, HTTP API, Lambda, DynamoDB và workflow giữ nguyên stack `cloudhub-pages-live` tại `ap-southeast-1`, UPDATE_COMPLETE. AWS Support xác nhận tài khoản được xác minh và mở CloudFront lúc 12:39 ngày 12/10 theo giờ Việt Nam.

## Đăng nhập và giao diện

Domain Hosted UI timeout từ máy triển khai và trình duyệt người dùng. Người dùng đã phê duyệt đăng nhập API Cognito cho app client hiện tại, không cấp thêm IAM/root. Giao diện gửi mật khẩu trực tiếp bằng HTTPS tới Cognito, xử lý mật khẩu lần đầu/MFA và refresh; chỉ token vào sessionStorage. Issuer/audience/chữ ký/access-token, trạng thái tài khoản và owner/admin vẫn được kiểm tra.

Đã kiểm tra hai chủ thể thử thật: đăng nhập thành công, tài khoản thường bị từ chối quản trị HTTP 403. Trình duyệt hiển thị danh mục, hạn mức, môi trường và quản trị từ CloudFront. Sáu file public trả HTTP 200 đúng SHA-256; S3 trực tiếp trả 403. Theo yêu cầu ngày 12/10, GitHub chỉ giữ mã và kiểm tra mã; workflow xuất bản Pages đã gỡ. Dùng URL CloudFront cho website.

Tài khoản quản trị `24020341@vnu.edu.vn` vẫn FORCE_CHANGE_PASSWORD; chủ tài khoản tự nhập mật khẩu tạm từ thư mời và tự đặt mật khẩu mới. Không có mật khẩu người thật trong repo/báo cáo. Xem [phiếu thay đổi đăng nhập](cognito-api-signin-review.md).

## Mẫu và kiểm thử thật

- T07: hai vòng độc lập tạo → ghi/đọc → sao lưu → thu hồi nguồn → restore mới → SHA-256 → thu hồi restore, đều PASS. Nguồn/restore DELETED, audit không residual/unknown. Gate đã mở.
- T09: hai vòng độc lập đã PASS; có dữ liệu, backup/restore, kiểm owner và audit sạch. Gate đã mở. API dữ liệu của môi trường mới chấp nhận scope API Cognito của cùng pool/client; kiểm lại với phương thức đăng nhập mới có minh chứng riêng.
- T01: đang kiểm lại. Máy và dữ liệu SSM hoạt động; SSH một máy thử đã đạt sau cập nhật IP /32 cho cùng máy triển khai. Máy tiếp theo timeout trước khi có gói SSH tới ENI dù sshd/rule đúng; đang chẩn đoán đường mạng. Không mở gate từ kiểm thử một phần.
- T02: CloudFront đã mở quyền, chờ vòng đời thật theo thứ tự T01–T04.
- T03/T04/T05/T08: có mã và lint/SDK tests; chưa đủ hai vòng AWS thật.
- T06: đã build source của nhóm trên EC2 tạm, kiểm health, đẩy ECR tag immutable và khóa digest cùng account/region. Ảnh Python Alpine mới scan COMPLETE, không findings. Hai builder đã thu hồi; giữ repo/image để thử Fargate. Chưa mở gate trước hai vòng thật.
- T10: Redshift Serverless trả SubscriptionRequiredException trong Free Plan. Không tự nâng Paid Plan.

T07 có 12 lệnh dài HTTP 202 dưới 2 giây, cao nhất 1.650,48 ms. T09 có 16 lệnh dài dưới 2 giây, cao nhất 1.434,99 ms. Chưa chứng minh SLA toàn bộ mẫu/cold start.

## Tải, hết hạn và vận hành

Phép đo mới 12/10 dùng 20 Cognito subjects riêng, 200 yêu cầu, đồng thời 20: 169 HTTP 200, 31 lỗi, p95 209,30 ms. CloudWatch có 31 throttles trong cửa sổ tương ứng; quota Lambda region vẫn 10. read_target_met=false. Không bỏ lỗi để tính đạt. Phép đo trước: 192/200, 8 throttles, p95 665,55 ms; cũng không đạt.

Yêu cầu tự động tăng quota 30 bị AWS từ chối vì phải lớn hơn default quota 1000. Không yêu cầu 1001, không mở case hỗ trợ mới hay nâng Paid Plan. Cần AWS giải quyết giới hạn tài khoản để nghiệm thu tải.

Fixture T07 cũ có timer riêng kích hoạt sau deadline 6 giây. CloudTrail xác nhận StartExecution do ExpiryFunctionRole; operation tự động SUCCEEDED, backup AVAILABLE, source DELETED, audit sạch. Observer liên tục đã dừng trước đó, nên báo cáo ghi rõ dựa vào timestamp AWS bền vững và CloudTrail. Phép thử fallback mới dùng phiên 1 giờ tự nhiên, chỉ tắt timer cá nhân của fixture; đang chờ quét 5 phút. Không sửa deadline hoặc thu hồi tay để tính đạt.

SNS email quản trị CONFIRMED; AWS nhận cảnh báo thử và trả Message ID, chưa xác minh thư đến hộp thư. Một T09 CREATE_FAILED cũ còn cần xử lý vì metadata giữ một nguồn không tạo được; không xóa mù hoặc bỏ final-backup guard. Bản sao thử nghiệm giữ 7 ngày và vẫn có thể trừ credits. Cost Explorer chưa bật, chưa đối soát chi phí cuối cùng.

## Nguồn và ngân sách

Nguồn/API 0.4.2. Đã đạt 154 Python tests, 14 DOM tests, compile/syntax/OpenAPI/cfn-lint. Kiểm thử localhost chạy với quyền mạng cục bộ sau khi sandbox chặn socket; không bỏ qua test. Đây là local/mock, tách riêng AWS thật.

Giữ Free Plan, ngân sách 120 USD credits / 0 USD tiền mặt đã duyệt. Credits đầu phiên đọc được 182,95 USD, có độ trễ billing và không phải hard cap. GitHub chỉ gồm file văn bản. Credentials, ảnh, ZIP, dependencies, cấu hình riêng và evidence chi tiết giữ ngoài Git.
