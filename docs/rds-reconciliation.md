# Kiểm kê RDS sau thu hồi

Inventory lưu PhysicalResourceId để đối chiếu sau xóa. RDS discovery trước đây gọi DescribeDBInstances cho cả tên database đã xóa, khiến lỗi DBInstanceNotFound có thể ngăn workflow đến bước audit cuối.

Discovery nay chấp nhận riêng DBInstanceNotFound/DBInstanceNotFoundFault và giữ nguyên resources, source_ids, db_id và data_source. Lỗi truy cập, throttling hoặc lỗi dịch vụ vẫn được ném ra. Giữ source_ids bảo đảm CREATE_FAILED không thể bỏ bước final backup dựa vào một lần discovery thiếu database. Audit cuối vẫn kiểm tra từng tài nguyên và bản sao trước khi trả hạn mức.

Ba tests kiểm tra: giữ inventory khi AWS xác nhận thiếu database, không coi lỗi không rõ là đã xóa, và không bỏ final backup với RDS CREATE_FAILED. Bộ kiểm tra toàn dự án đạt 167 Python tests, 14 DOM tests cùng compile/syntax/OpenAPI/CloudFormation lint.

Ngày 12/10/2026 đã áp dụng gói Lambda qua CloudFormation, toàn bộ thay đổi Modify/Replacement=False, không đổi IAM/Cognito hoặc dữ liệu. Chưa coi tests local là minh chứng hai vòng RDS thật; gate T03/T04 vẫn chờ kết quả lifecycle AWS.
