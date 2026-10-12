# Thu hồi T09 lỗi trước khi sẵn sàng

CloudFormation có thể ghi PhysicalResourceId là tên bảng dự kiến khi CreateTable thất bại. Inventory trước đây coi tên đó là nguồn dữ liệu tồn tại, nên final backup không thể chạy và môi trường giữ trạng thái cần quản trị.

DataDriver hiện chỉ loại tên bảng khỏi source_ids khi môi trường T09 chưa từng READY, stack đã CREATE_FAILED/ROLLBACK_COMPLETE/ROLLBACK_FAILED, và DescribeTable trả chính xác ResourceNotFoundException cho tên bảng của môi trường. PhysicalResourceId vẫn được giữ để audit. AccessDenied, lỗi dịch vụ, nguồn tồn tại hoặc môi trường từng READY đều không dùng ngoại lệ này.

Audit đọc lại inventory trước khi đối chiếu. Sau audit, chỉ trường hợp có bằng chứng nguồn thiếu và không có nguồn khác mới được thu hồi từ RESIDUAL. Worker kiểm lại sự tồn tại ở mỗi bước DELETE; nếu bảng xuất hiện, giữ nguồn và yêu cầu sao lưu. Không tạo recovery point AVAILABLE giả cho bảng không tồn tại.

Ngày 12/10/2026, bản sửa đã triển khai qua CloudFormation, không thay thế tài nguyên, không đổi IAM/Cognito. Fixture thử cũ env-511e90b81b6105bdd6c19d6c được audit bằng tài khoản thử của đúng chủ sở hữu rồi thu hồi qua API quản trị: operation SUCCEEDED, môi trường DELETED, quota_released=true, residual/unknown rỗng, recovery_point=null. Tài khoản example.invalid được khóa lại ngay. Không thay mật khẩu hoặc trạng thái tài khoản người thật.

10 tests bảo vệ các nhánh dữ liệu tồn tại/không rõ, nguồn từng READY, nguồn xuất hiện lại và audit. Toàn bộ 164 Python tests, 14 DOM tests cùng compile/syntax/OpenAPI/CloudFormation lint đạt. Evidence chi tiết chứa thông tin tài khoản triển khai nằm ngoài Git.
