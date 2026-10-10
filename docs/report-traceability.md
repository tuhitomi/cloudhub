# Đối chiếu A01–A25

Nguồn: CloudHub_Bao_cao_tien_do_Nhom_1_final.docx, file gốc không sửa. Giữ nguyên mã/tên/giờ/phụ trách. Báo cáo gốc 800 giờ công việc + 80 dự phòng = 880; yêu cầu hiện tại 500 + 60 = 560, chênh 320 giờ. Chưa có giờ người thực tế để chứng minh lịch 10 tuần; không giảm số gốc trên giấy.

| Mã | Công việc gốc | Giờ gốc | Phụ trách gốc | Trạng thái bàn giao |
|---|---|---:|---|---|
| A01 | Yêu cầu và giao diện phân hệ. Chốt cấu trúc mẫu, OpenAPI và tiêu chí nghiệm thu. | 16 | TV2; cả nhóm | Có mã/cấu hình/tài liệu; chưa nghiệm thu AWS |
| A02 | Kho mã và CI. Chuẩn hóa môi trường kiểm thử, rà soát và triển khai chung. | 20 | TV1; TV2 | Có mã/cấu hình/tài liệu; chưa nghiệm thu AWS |
| A03 | IaC nền tảng. Thử quyền, quota; cấu hình role, mạng và region. | 32 | TV3; TV4 | Có mã/cấu hình/tài liệu; chưa nghiệm thu AWS |
| A04 | Đăng nhập và phân quyền. Dùng Cognito; kiểm tra quyền sở hữu, quota và tài khoản quản trị. | 32 | TV2; TV1 | Có mã/cấu hình/tài liệu; chưa nghiệm thu AWS |
| A05 | Giao diện dùng chung. Form theo cấu trúc mẫu; các bước tạo, dự toán, trang quản lý và tiến trình. | 48 | TV1; TV2 | Có mã/cấu hình/tài liệu; chưa nghiệm thu AWS |
| A06 | Dữ liệu và tác vụ. Cấu trúc DynamoDB, danh sách tài nguyên, khóa và chống yêu cầu trùng. | 32 | TV2; TV4 | Có mã/cấu hình/tài liệu; chưa nghiệm thu AWS |
| A07 | Điều phối triển khai. Tạo stack, kiểm tra hoạt động, hoàn tác và lưu trạng thái tác vụ dài. | 40 | TV3; TV4 | Có mã/cấu hình/tài liệu; chưa nghiệm thu AWS |
| A08 | Khung cuối phiên. Scheduler khóa và kết thúc phiên, sao lưu, khôi phục và đọc lại hạn phiên. | 40 | TV4; TV2 | Có mã/cấu hình/tài liệu; chưa nghiệm thu AWS |
| A09 | Dự toán chi phí. Đơn giá, giả định giữ dữ liệu và kịch bản mở rộng cấu hình mẫu. | 36 | TV2; TV1 | Có mã/cấu hình/tài liệu; chưa nghiệm thu AWS |
| A10 | T01 Máy Linux. EC2, EBS; truy cập, snapshot, khôi phục và thu hồi. | 32 | TV3; TV4 | Có mã/cấu hình/tài liệu; chưa nghiệm thu AWS |
| A11 | T02 Website tĩnh. Tải nội dung lên, truy cập HTTPS, sao lưu, khôi phục và thu hồi. | 24 | TV4; TV1 | Có mã/cấu hình/tài liệu; chưa nghiệm thu AWS |
| A12 | T03 Web và cơ sở dữ liệu. Ứng dụng minh họa, RDS, bí mật và khôi phục dữ liệu. | 40 | TV3; TV2 | Có mã/cấu hình/tài liệu; chưa nghiệm thu AWS |
| A13 | T04 Web chịu tải cao. ALB, ASG; giới hạn co giãn và vòng đời dữ liệu. | 40 | TV3; TV1 | Có mã/cấu hình/tài liệu; chưa nghiệm thu AWS |
| A14 | T05 Website trên máy. Dùng lại EC2; cấu hình web server, nội dung và khôi phục. | 20 | TV1; TV3 | Có mã/cấu hình/tài liệu; chưa nghiệm thu AWS |
| A15 | T07 Kho file. Tải lên, tải xuống, liên kết có hạn; danh sách bản sao và khôi phục. | 24 | TV2; TV1 | Có mã/cấu hình/tài liệu; chưa nghiệm thu AWS |
| A16 | T06 Docker. ECS Fargate; kiểm tra hoạt động và bảo vệ dữ liệu S3 đã đăng ký. | 36 | TV3; TV1 | Có mã/cấu hình/tài liệu; chưa nghiệm thu AWS |
| A17 | T09 API serverless. Lambda API, DynamoDB; kiểm tra gọi API và khôi phục. | 32 | TV2; TV3 | Có mã/cấu hình/tài liệu; chưa nghiệm thu AWS |
| A18 | T08 Ổ dùng chung. EFS cho hai máy; sao lưu, khôi phục và gỡ phụ thuộc. | 40 | TV4; TV3 | Có mã/cấu hình/tài liệu; chưa nghiệm thu AWS |
| A19 | T10 Kho phân tích. Nạp dữ liệu, truy vấn mẫu, tạo snapshot và khôi phục. | 40 | TV4; TV2 | Có mã/cấu hình/tài liệu; chưa nghiệm thu AWS |
| A20 | Theo dõi và quản trị. Chỉ số, nhật ký, thông báo, quota, quản trị và báo cáo phiên. | 36 | TV1; TV2 TV4 | Có mã/cấu hình/tài liệu; chưa nghiệm thu AWS |
| A21 | Kiểm tra còn sót và hạn giữ dữ liệu. Đối chiếu tài nguyên, thử lại thu hồi, hết hạn bản sao và bảo vệ tài nguyên chung. | 48 | TV4; TV2 TV3 | Local tests đã chạy; audit/retention AWS chưa chạy |
| A22 | Kiểm thử hệ thống. Vòng đời 10 mẫu; quyền chéo, khởi động lại, hết thời gian chờ và lỗi sao lưu, thu hồi. | 48 | TV4; cả nhóm | Harness đủ 10 mẫu; 0/20 vòng AWS thật |
| A23 | Đo hiệu năng và trải nghiệm. Thử tải API, ASG; kiểm tra form và trang quản lý. | 20 | TV1; TV4 | Có benchmark/expiry scripts; chưa đo SLA hoặc review UI trên AWS |
| A24 | Bàn giao. Tài liệu, mã nguồn, IaC, kiểm thử, số liệu, minh chứng và kịch bản trình diễn. | 24 | TV2; cả nhóm | Gói source và hướng dẫn bàn giao; chưa có demo AWS |
| A25 | Dự phòng học dịch vụ, tích hợp và sửa lỗi phát sinh. Theo dõi quỹ dự phòng. | 80 | Nhóm trưởng; cả nhóm | Giữ nguyên 80 giờ dự phòng gốc; chưa ghi giờ đã dùng |
