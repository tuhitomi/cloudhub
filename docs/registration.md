# Đăng ký và xác thực tài khoản CloudHub

Người dùng đăng ký bằng email và mật khẩu, xác thực mã gửi qua email rồi đăng nhập. Đây là tài khoản CloudHub; không cần tài khoản AWS hoặc access key. Quên mật khẩu và đặt lại mật khẩu dùng mã gửi đến email đã xác thực. Mật khẩu đi trực tiếp tới API HTTPS Cognito, được xóa khỏi trường nhập sau gửi và không vào API CloudHub, DynamoDB, log hoặc Git.

Giữ user pool và app client hiện tại. Bật self-registration, tự xác thực email bằng mã và chỉ cho app client ghi thuộc tính `email`; không cho người dùng ghi `email_verified`, nhóm quản trị hoặc quota. Thay đổi email yêu cầu xác thực trước khi cập nhật thuộc tính. Không có trigger tự xác nhận hoặc bỏ qua mã email.

Access token Cognito không chứa trạng thái email đáng tin cậy cho việc kiểm tra này. Backend đọc `AdminGetUser` trong pool cấu hình, đối chiếu `sub` với JWT đã được API Gateway xác minh và yêu cầu tài khoản CONFIRMED, đang mở, `email_verified=true`. Không tin trường email/role/quota do trình duyệt gửi. Tất cả API quản lý môi trường bị chặn nếu chưa xác thực; `GET /account` cho phép xem trạng thái để hoàn tất xác thực. Lỗi provider chặn yêu cầu, không tự cho qua.

Hồ sơ `U#<sub>` liên kết Cognito `sub`; hồ sơ mới có vai trò `user`. Quyền quản trị vẫn cần nhóm Cognito `admin` được đối chiếu lại trực tiếp trên provider. Quota `Q#<sub>` khởi tạo trong cùng transaction ở yêu cầu đầu sau xác thực. Gọi đồng thời hoặc đăng nhập lại không đặt lại quota, số môi trường, khoản đã dự toán hay hạn mức quản trị đã chỉnh.

Quản trị đặt quota mặc định trong giao diện qua `GET/POST /admin/signup-defaults`, được lưu tại `S#SIGNUP_DEFAULTS`. Khi chưa chỉnh, dùng UserLimit/BudgetMilliusd hiện có của stack. Quota mặc định không vượt hạn mức hệ thống và áp dụng lúc khởi tạo quota của tài khoản mới. Tài khoản đã có quota giữ nguyên. Ngân sách dự toán không phải giới hạn hóa đơn AWS.

Kiểm thử local/SDK/DOM bao gồm email trùng, mã sai/hết hạn, gửi lại mã và giới hạn nhấn liên tiếp, đăng nhập sai/chưa xác nhận, đặt lại mật khẩu sai/hết hạn, xóa mật khẩu khỏi trường nhập, chặn email chưa xác thực, đối chiếu `sub`, provider lỗi, cấp quota đồng thời, không tự chọn admin/quota và giữ hạn mức cũ. Kết quả local không chứng minh email đã đến hộp thư. Kiểm thử nhận mã thật cần chủ hộp thư nhập mật khẩu/mã trên website; không dùng xác nhận admin để giả minh chứng xác thực email.

Luồng tuân theo API [SignUp](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_SignUp.html), [ConfirmSignUp](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_ConfirmSignUp.html) và [hướng dẫn xác thực tài khoản](https://docs.aws.amazon.com/cognito/latest/developerguide/signing-up-users-in-your-app.html).
