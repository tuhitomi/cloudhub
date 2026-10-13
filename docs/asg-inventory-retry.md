# T04: kiểm kê và quyền sử dụng launch template

Ngày 13/10/2026, kiểm thử AWS thật gặp hai lỗi trong lần tạo T04 đầu tiên. CloudFormation đã có physical name của Auto Scaling Group trong khi nhóm chưa được tạo; `DescribePolicies` trả `Group … not found`, làm workflow dừng trước khi thấy lỗi gốc. Stack sau đó `CREATE_FAILED` vì role triển khai thiếu `ec2:RunInstances` trên ARN launch template. RDS đã tạo thành công và được giữ lại; lần tạo dở không được tính là một vòng đời đạt.

Kiểm kê nay gọi `DescribeAutoScalingGroups` cho đúng tên trước khi đọc policy. Nhóm chưa tồn tại thì giữ tên đã kiểm kê và tiếp tục chờ CloudFormation. Nhóm có thật phải khớp tag environment/owner; lỗi AccessDenied và lỗi chưa xác nhận vẫn được giữ. Nếu nhóm bị xóa giữa hai lần đọc, chỉ chấp nhận vắng mặt sau khi lookup mới xác nhận không còn nhóm.

Role `EnvironmentCfRole` được bổ sung quyền `ec2:RunInstances` chỉ trên launch template trong account/region của stack, có `cloudhub:managed=true` và `cloudhub:environment=env-*`. AMI, hai subnet đã duyệt, role được truyền và permissions boundary giữ phạm vi cũ. Không cấp RunInstances cho người dùng website hoặc mở ingress. AWS kiểm quyền launch template và quyền truyền instance role khi tạo Auto Scaling Group; xem [tài liệu AWS](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-launch-template-permissions.html).

Sáu kiểm thử bảo vệ trường hợp chưa tạo, alarm trùng, AccessDenied, lỗi chưa xác nhận, nhóm sai owner và phạm vi IAM. Bản sửa được triển khai bằng changeset giữ dữ liệu/identity, không thay thế tài nguyên. Kiểm thử tiếp tục qua API retry trên cùng operation/environment và template đã lưu, giữ quota và lịch sử lỗi; gate T04 chỉ mở khi đủ hai vòng thật và audit sạch.
