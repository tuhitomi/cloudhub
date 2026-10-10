# Phiếu xem trước triển khai — chưa tạo tài nguyên

Account quan sát trên Console: **133130236086**; region: **ap-southeast-1**. Credits hiển thị **183,66 USD**, Free Plan còn 151 ngày, hết hạn hiển thị 07/03/2027. Chi phí tháng hiện tại hiển thị 1,73 USD thuộc tài nguyên đang có, chưa xác định chủ sở hữu. Không đụng tới tài nguyên đó.

CloudShell báo tài khoản đang xác minh, có thể mất tới hai ngày. [Ảnh thật](screenshots/aws-cloudshell-blocked.jpg) và [ghi nhận](aws-account-observation.json). Việc này chặn thực thi SDK/CLI qua CloudShell. Phiên đăng nhập browser không tự cấp credentials cho terminal tại máy.

## Dự toán

Giá công khai AWS được tải ngày 09/10/2026 từ Price List Singapore, có SKU, RateCode, tier, đơn vị và phép đổi trong `config/prices.ap-southeast-1.review.json`. `approved=false`; không khẳng định đơn giá đã được người vận hành phê duyệt. Giá CloudFront theo US/EU edge của PriceClass_100, không gán giá origin Singapore cho edge.

Hai vòng mỗi mẫu gồm nguồn + môi trường restore + dự phòng một full quote cho manual backup/cleanup: 6 quote một giờ/mẫu. Mỗi quote đã gồm 20% dự phòng; chưa trừ free allowance hoặc credits. Tổng 99,438 USD; thêm 10 USD dự phòng hạ tầng chung trong cửa sổ thử và đề xuất 120 USD gross dùng credits, 0 USD tiền mặt. Dự toán 30 USD ban đầu chưa đủ bao trùm kế hoạch này. T10 chiếm 67,050 USD của dự toán 99,438 USD vì giả định 8 RPU chạy liên tục 2,5 giờ mỗi quote; đây là cách tính thận trọng, không phải hóa đơn thực tế.

`control_plane_session=0.10 USD` là khoản phân bổ kế hoạch cho workflow/Lambda/API/DynamoDB/Scheduler/metrics, **không phải đơn giá AWS**. Dự phòng hạ tầng chung 10 USD chưa được xác minh bằng billing. Chi phí có độ trễ; quota không là hard billing cap. Lưu lượng public, lỗi giữ nguồn, log quá mức, hoạt động ngoài cửa sổ thử, thuế/chuyển đổi tiền và tài nguyên cũ có thể nằm ngoài dự toán. Credits có hạn; không nâng Paid Plan để vượt giới hạn dịch vụ.

## Tài nguyên và quyền

Control plane: S3 frontend riêng tư + CloudFront/OAC; Cognito pool/client/domain/admin group; HTTP API JWT; 4 Lambda; DynamoDB/GSI/PITR; Standard Step Functions; Scheduler group và quét 5 phút; SSM bảng giá; IAM service roles + application boundary; shared S3 backup/vault/Backup roles; SQS DLQ; CloudWatch log/metric/alarms/dashboard; SNS chưa subscription. SAM packaging bucket riêng; T06 cần ECR repository/image digest được duyệt.

Network tùy chọn `infra/network.json`: 1 VPC, 2 public + 2 DB subnets ở 2 AZ, Internet Gateway, 2 route tables và associations; không NAT. Kiểm CIDR không trùng trước khi tạo. Network dùng chung, không được thu hồi cùng môi trường.

Danh sách từng mẫu ở [templates.md](templates.md); review JSON được sinh bằng `prepare_deployment.py --draft`, chứa config, quotes, các resource types và tham số để xem. Khi còn blockers, không sinh `sam-parameters.json` dùng triển khai.

Quyền cần review: IAM tạo role/PassRole, service-linked roles, CloudFormation/SAM packaging và service APIs từng mẫu. Application roles có boundary và inline policy đúng nguồn. Một số create/describe cần wildcard theo region/ID AWS sinh; AWS Backup dùng managed backup/restore policies còn rộng. Cần kiểm quyền thật trước pilot, không coi lint là bằng chứng least privilege. Tạo IAM/security access cần xác nhận tại thao tác.

## Thu hồi

Final backup AVAILABLE → xóa stack sở hữu → audit exact IDs và nguồn/bản sao → báo deleted/retained/residual/unknown/billable. Backup lỗi giữ nguồn và quota. Giữ backups 7 ngày, kho/table quản lý có Retain; chúng vẫn có thể có phí. Chỉ xử lý từng ID đã xác minh owner/stack. Không xóa wildcard toàn tài khoản, network chung, ECR hay packaging bucket cùng environment. Xem [deployment.md](deployment.md).
