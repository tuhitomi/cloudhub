# Phiếu xem trước đăng nhập qua API Cognito

Ngày 12/10/2026. Người dùng đã phê duyệt phương án và CloudFormation cập nhật thành công `UPDATE_COMPLETE`. Giao diện mới đã được xuất bản lên CloudFront; sáu file trả HTTP 200 đúng SHA-256, truy cập trực tiếp S3 bị từ chối 403.

Domain Hosted UI đang timeout từ cả máy triển khai và trình duyệt của người dùng. Cognito IDP API vẫn đọc được user pool. Frontend AWS đang chạy tại https://d3qujq9kfq5gca.cloudfront.net/.

Đề xuất thêm `ALLOW_USER_PASSWORD_AUTH` vào app client hiện tại `7o8f1hkkid5kmjbbcmek5elup`, pool `ap-southeast-1_poFnX7aGC`. Frontend gửi email/mật khẩu trực tiếp bằng HTTPS tới `cognito-idp.ap-southeast-1.amazonaws.com`; mật khẩu không đi qua backend CloudHub, không vào sessionStorage, Git hoặc nhật ký. Lần đăng nhập đầu chờ người dùng tự chọn mật khẩu mới; MFA được phản hồi qua challenge, bước chưa hỗ trợ bị từ chối.

HTTP API quản lý và API dữ liệu T09 chấp nhận một trong hai scope: `cloudhub/use` cho OAuth hiện tại, hoặc `aws.cognito.signin.user.admin` cho SDK. Scope thứ hai là quyền Cognito để người dùng thao tác trên chính tài khoản website của mình. Issuer, app-client audience, chữ ký JWT, `token_use=access`, trạng thái tài khoản, quyền sở hữu và kiểm tra nhóm admin hiện tại tiếp tục được thực thi. Không cấp thêm quyền IAM/root của tài khoản AWS.

User pool, app client, nhóm quản trị, tài khoản hiện có, DynamoDB quản lý, hạ tầng và các bản sao được giữ nguyên. Không đổi Free Plan. Code cũ và template trước cập nhật được lưu ngoài Git để rollback. Các tài nguyên hiện có không cần thay thế.

Đã đạt 154 kiểm thử Python và 14 kiểm thử DOM cùng compile/syntax/OpenAPI/cfn-lint. Kiểm tra AWS thật đã xác thực hai chủ thể thử riêng, access token đúng pool/client và scope SDK; tài khoản thường bị từ chối quản trị 403. Trình duyệt đã đăng nhập từ website CloudFront, hiển thị danh mục, hạn mức, môi trường và màn hình quản trị. Hai vòng đời của các mẫu được đánh giá bằng minh chứng riêng; kiểm thử local không dùng để mở gate.

File chính: `frontend/auth.js`, `frontend/app.js`, `scripts/portal_infra.py`, `backend/cloudhub/data_template.py`, `scripts/access_token.py`, `tests/auth.test.mjs`, `tests/test_frontend_migration.py`.
