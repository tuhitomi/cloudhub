# Khắc phục tạo EFS và thử lại nguồn đã tạo một phần

Kiểm thử T08 ngày 13/10/2026 tạo được filesystem mã hóa, nhưng hai mount target bị AWS từ chối quyền. Hai máy EC2 phụ thuộc mount target nên chưa được tạo. Chưa có vòng T08 đạt yêu cầu tại thời điểm phát hiện lỗi này.

EFS cần `ec2:CreateNetworkInterface` khi tạo mount target, và `ec2:DeleteNetworkInterface` khi thu hồi. Quyền tạo chỉ nằm trên deployment role, trong account/region hiện hành và hai subnet đã duyệt. Quyền xóa giới hạn cả VPC và hai subnet đó. NFS vẫn chỉ nhận traffic từ security group của hai máy trong cùng môi trường; quyền của các máy và boundary của sinh viên được giữ.

Thao tác thử lại lần tạo cho T03/T04/T06/T08/T09 dùng chính stack có tag owner/environment đã kiểm tra. Stack mới được tạo bằng `DisableRollback=true`; CloudFormation dùng template đã lưu và cùng role khi thử lại, không nhận template người dùng. Stack đã hoàn thành cập nhật vẫn phải qua kiểm tra dữ liệu và readiness trước khi bắt đầu phiên. Khóa, workflow generation và giới hạn ba lần thử lại tiếp tục áp dụng. Các bước CREATE của restore và các mẫu khác vẫn bị từ chối.

AWS thực tế từ chối `UpdateStack` đối với fixture cũ tạo bằng `OnFailure=DO_NOTHING`, dù cách đó cũng giữ tài nguyên khi thất bại. Backend từ chối rõ ràng trường hợp cũ này và giữ nguồn. Quản trị phải bảo vệ filesystem bằng bản sao AWS Backup thật trước khi thu hồi phần tạo dở; vòng thất bại và bản sao vận hành đó không được tính là vòng T08 đạt yêu cầu.

Kiểm thử local kiểm tra từ chối sai chủ sở hữu, giữ quota và stack cũ, không gửi lại update đang chạy, bảo toàn tài nguyên khi lỗi tiếp và giới hạn IAM. Việc mở gate T08 vẫn yêu cầu hai vòng AWS thật có checksum, backup/restore và cleanup riêng.

Nguồn chính thức: [EFS CreateMountTarget](https://docs.aws.amazon.com/efs/latest/APIReference/API_CreateMountTarget.html), [EFS DeleteMountTarget](https://docs.aws.amazon.com/efs/latest/APIReference/API_DeleteMountTarget.html), [CloudFormation giữ tài nguyên thành công và thử lại](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/stack-failure-options.html), [EC2 action/resource/condition](https://docs.aws.amazon.com/service-authorization/latest/reference/list_ec2.html).
