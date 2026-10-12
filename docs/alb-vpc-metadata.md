# Quyền đọc metadata VPC để tạo ALB

Kiểm thử T06 ngày 12/10/2026 bị AWS từ chối khi tạo ALB vì role CloudFormation thiếu `ec2:GetSecurityGroupsForVpc`. Các tài nguyên khác báo `Resource creation cancelled`; đây không phải bằng chứng từng tài nguyên đều thiếu quyền riêng.

Bản sửa thêm đúng một action đọc vào policy của `EnvironmentCfRole`, giới hạn ARN của VPC đã cấu hình trong cùng account/region và điều kiện `aws:RequestedRegion`. Không thêm action sửa mạng vào bản sửa này hoặc thêm quyền cho `EnvironmentBoundary` của ứng dụng sinh viên. AWS mô tả API này là đọc các security group có thể sử dụng trong VPC: [GetSecurityGroupsForVpc](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSecurityGroupsForVpc.html).

Môi trường T06 tạo dở có bucket riêng tồn tại nhưng chưa có object/version và chưa chạy task. Thu hồi qua API CloudHub vẫn tạo bản sao S3 thật trước khi xóa stack; bản sao rỗng được ghi đúng là rỗng, không được tính thành vòng kiểm thử đã đạt. Mở gate T06/T04 vẫn cần hai vòng đời độc lập và kiểm kê sạch trên AWS.

Kiểm thử local bảo vệ scope VPC, region, role triển khai và việc không đưa action mới vào boundary sinh viên. Minh chứng chi tiết IAM change set, lỗi tạo và thu hồi giữ ngoài Git cùng các artifact AWS khác.
