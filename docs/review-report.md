# Báo cáo rà soát CloudHub

Ngày tổng hợp: 10/10/2026 (Asia/Bangkok). Bằng chứng kiểm thử và quan sát AWS được thu thập ngày 09/10/2026. Phạm vi: mã nguồn, frontend, API, phân quyền, vòng đời môi trường, sao lưu/khôi phục, hạ tầng, kiểm thử và tài liệu trong `outputs/cloudhub`.

## Kết luận

CloudHub đã có nền tảng triển khai đáng kể cho đồ án, nhưng **chưa đủ điều kiện nghiệm thu vận hành hoặc mở cho nhiều người dùng**. Chờ AWS xác minh CloudFront chỉ giải quyết một trở ngại triển khai; vẫn cần sửa các lỗi về bảo vệ bản sao và phân quyền dưới đây.

Bộ kiểm thử hiện có đều đạt, nhưng các tình huống bổ sung đã tái hiện sáu vấn đề. Kết quả kiểm thử đạt không đồng nghĩa các luồng nghiệp vụ và quyền truy cập đã an toàn trong mọi trường hợp.

## Bằng chứng và giới hạn

| Hạng mục | Kết quả |
|---|---|
| Kiểm thử Python | 108 đạt, không bỏ qua; chủ yếu local/mock |
| Kiểm thử frontend DOM | 8 đạt |
| Kiểm tra tĩnh | Python compile, cú pháp JavaScript, OpenAPI và 21 nhóm cfn-lint đều đạt |
| npm audit | Không phát hiện lỗ hổng đã biết trong các dependency được kiểm tra; không phải chứng nhận bảo mật ứng dụng |
| Tái hiện bổ sung | 7 tình huống xác nhận 6 vấn đề; R01 có hai biến thể |
| Chu kỳ AWS nghiệm thu | 0/20; chưa đo chỉ tiêu trên AWS thật |
| Quan sát giao diện | Đã đọc mã và chạy DOM tests; trình duyệt không kết nối được localhost, nên chưa xác minh trực quan bố cục, mobile hay accessibility |

Bằng chứng: [kết quả kiểm thử hiện có](local-check-results.json), [mã tái hiện](review-evidence/reproduce_findings.py), [kết quả tái hiện](review-evidence/reproduction-results.json). Script tái hiện chạy thành công nghĩa là xác nhận được lỗi, không phải lỗi đã được sửa. Các tình huống bổ sung dùng mock hoặc phân tích cấu hình, không khai thác tài khoản AWS thật.

## Các vấn đề cần sửa

P1: cần sửa trước khi mở dùng chung. P2: cần sửa trước nghiệm thu chức năng liên quan.

### R01 — P1: Có thể xóa bản sao cuối cùng trong khi thu hồi vẫn dựa vào bản sao đó

Vị trí: `backend/cloudhub/portal.py:125–144`, `backend/cloudhub/worker.py:86–95`.

Sau khi bước BACKUP hoàn tất, recovery point chuyển AVAILABLE và tác vụ thu hồi chuẩn bị DELETE. API xóa bản sao chỉ kiểm tra khóa restore/export/delete, chưa bảo vệ bản sao đang được tác vụ thu hồi sử dụng. Worker DELETE thấy có recovery_point nhưng không kiểm tra lại bản sao còn tồn tại và khả dụng.

Tái hiện: bản sao đã DELETED nhưng worker vẫn gọi xóa nguồn. Biến thể thứ hai: thu hồi lỗi tại DELETE, người dùng xóa bản sao rồi thử lại; worker tiếp tục xóa nguồn. Đây là nguy cơ mất cả dữ liệu gốc lẫn bản sao bảo vệ.

Khuyến nghị: khóa hoặc tham chiếu bản sao theo tác vụ thu hồi, bảo vệ bằng giao dịch có điều kiện; chặn cả xóa thủ công và dọn hết hạn; kiểm tra lại recovery point trước xóa nguồn. Nếu bản sao không còn hợp lệ, giữ nguồn và yêu cầu tạo bản sao mới.

### R06 — P1: Role dùng chung của T01/T05 có quyền đọc Parameter Store quá rộng

Vị trí: `scripts/generate_infra.py:57–60`; instance profile được gắn vào máy của T01/T05.

Cấu hình gắn managed policy AmazonSSMManagedInstanceCore, không có boundary hoặc Deny thu hẹp việc đọc Parameter Store. Policy này cho phép `ssm:GetParameter` và `ssm:GetParameters` với Resource `*`. Người dùng có SSH vào máy có thể dùng credentials của instance role; IMDSv2 không ngăn người dùng bên trong máy làm việc đó. [Policy chính thức của AWS](https://docs.aws.amazon.com/aws-managed-policy/latest/reference/AmazonSSMManagedInstanceCore.html).

Hệ quả có điều kiện: có thể đọc các parameter ngoài môi trường của mình khi biết tên và không có lớp Deny bên ngoài. CloudFormation lưu private key của key pair do dịch vụ tạo tại `/ec2/keypair/{key_pair_id}`; khóa KMS mặc định `aws/ssm` cho phép các IAM principal trong tài khoản giải mã. Đây là đường truy cập cần chặn trước khi cấp SSH cho sinh viên. Không có private key thực tế nào được đọc trong đợt review. SCP và các Deny ngoài cấu hình dự án chưa được kiểm tra. [EC2 KeyPair](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-ec2-keypair.html), [quyền giải mã Parameter Store](https://docs.aws.amazon.com/systems-manager/latest/userguide/ps-restrict-decryption.html).

Khuyến nghị: dùng policy tối thiểu cho kênh quản lý SSM; bỏ quyền đọc parameter nếu không cần, hoặc giới hạn chính xác tài nguyên cần dùng. Bổ sung kiểm thử từ môi trường A không đọc được parameter của B.

### R02 — P1: Dọn bản sao EBS có thể báo thành công khi chưa kiểm tra tài nguyên AWS

Vị trí: `backend/cloudhub/aws_driver.py:313–315`.

`expire_backup` trả về True ngay khi metadata không có snapshot_id. Với trường hợp AWS Backup đã nhận job nhưng phản hồi bị mất hoặc timeout, job có thể hoàn tất sau đó mà bản ghi chưa có snapshot_id. Tái hiện nhánh này cho kết quả thành công với 0 lời gọi AWS.

Hệ quả: có thể đánh dấu bản sao hết hạn trong ứng dụng trong khi recovery point hoặc snapshot vẫn còn, tiếp tục phát sinh chi phí. Việc tồn tại tài nguyên mồ côi cụ thể chưa được xác nhận trên AWS.

Khuyến nghị: đối chiếu backup_job_id, trạng thái job và recovery point trong vault; chỉ kết luận đã xóa khi có bằng chứng phù hợp. Xử lý cả job còn chạy và thông tin chưa đầy đủ, không suy ra “không có tài nguyên” từ metadata thiếu.

### R03 — P1: Admin đã bị gỡ quyền vẫn có thể tạo mẫu chưa xác minh bằng JWT cũ

Vị trí: `backend/cloudhub/api.py:37–40,68`, `backend/cloudhub/service.py:52–55`, `backend/cloudhub/portal_api.py:13`.

Các route `/admin/` kiểm tra quyền admin hiện tại, nhưng POST `/environments` với `validation_run:true` dùng quyền admin lấy từ JWT. Tái hiện giả lập việc kiểm tra quyền hiện tại sẽ từ chối, nhưng endpoint vẫn trả 202 và không gọi kiểm tra đó.

Hệ quả: người vừa bị gỡ quyền admin có thể tiếp tục chạy mẫu dành cho xác minh khi token cũ còn hạn. Khuyến nghị kiểm tra quyền hiện tại cho mọi nhánh đặc quyền, bao gồm validation_run; bổ sung kiểm thử thu hồi quyền khi token chưa hết hạn.

### R04 — P2: Tài khoản bị khóa vẫn đọc được API trực tiếp của T09 với JWT còn hạn

Vị trí: `backend/cloudhub/data_api_app.py:6–18`; đối chiếu `backend/cloudhub/api.py:41–43`.

API chính kiểm tra trạng thái enabled của tài khoản, nhưng API dữ liệu T09 chỉ kiểm tra owner, trạng thái môi trường và thời hạn. Tái hiện tài khoản bị khóa vẫn nhận HTTP 200; handler chỉ đọc bản ghi E#env, không kiểm tra U#owner.

Khuyến nghị áp dụng kiểm tra trạng thái tài khoản nhất quán, đồng thời cập nhật quyền DynamoDB đủ hẹp để T09 đọc được đúng bản ghi tài khoản cần kiểm tra. Không chỉ dựa vào việc thu hồi token: bên chỉ xác minh chữ ký/hạn JWT có thể vẫn chấp nhận token đã bị thu hồi. [Tài liệu Cognito](https://docs.aws.amazon.com/cognito/latest/developerguide/token-revocation.html).

### R05 — P2: Thu hồi còn sót tài nguyên không tạo tín hiệu cảnh báo SNS

Vị trí: `backend/cloudhub/worker.py:96–105`, `scripts/generate_infra.py:156`.

Worker đặt môi trường thành RESIDUAL và needs_admin, nhưng kết thúc operation SUCCEEDED, trả failed=false và không phát sự kiện CloudHubOperationFailed. Alarm hiện tại dựa trên sự kiện lỗi đó, Lambda Errors hoặc DLQ; nhánh này không kích hoạt chúng.

Tái hiện có volume dư: môi trường RESIDUAL, operation SUCCEEDED, không có log cảnh báo. Vì vậy chỉ đăng ký email SNS vẫn chưa đủ để nhận cảnh báo cho tình huống này.

Khuyến nghị thêm sự kiện/metric và alarm riêng cho tài nguyên dư hoặc không xác định; sau triển khai phải thử đến bước email thực sự được nhận.

## Nhận xét theo từng phần của dự án

| Phần | Điểm đã có | Nhận xét / điều còn thiếu |
|---|---|---|
| Kiến trúc | API, trạng thái DynamoDB, workflow bất đồng bộ, cơ chế idempotency và kiểm soát cập nhật đồng thời | Nền tảng phù hợp đồ án quản lý vòng đời cloud; cần kiểm chứng hành vi khi AWS trả lỗi hoặc mất phản hồi |
| Chức năng | Danh mục 10 mẫu; tạo, gia hạn, thu hồi; sao lưu, khôi phục; lịch sử, lọc, hạn mức | Có mã và kiểm thử local; chưa được tính là đã vận hành đủ trên AWS |
| Quản trị | Xem môi trường, thu hồi, hạn mức, quản lý tài khoản | Cần khép kín thu hồi quyền giữa API chính, validation và API riêng của môi trường |
| Đăng nhập | Cognito và luồng đổi/quên mật khẩu có trong mã | Chưa kiểm chứng đầy đủ từ trình duyệt đến Cognito thật |
| Sao lưu | Có kiểm tra dữ liệu, khóa một số thao tác đồng thời, giữ nguồn khi backup lỗi | R01 là chốt chặn nghiệm thu bảo vệ dữ liệu; bản xuất của backup native có thể chỉ là metadata, không phải mọi loại đều tải được dữ liệu đầy đủ |
| UI/UX | Giao diện tiếng Việt, nhắc sắp hết hạn, điều khiển tác vụ lỗi, hiển thị hạn mức | Cần xem trực tiếp trên desktop/mobile; một số trạng thái kỹ thuật/JSON còn khó hiểu với người mới |
| Cảnh báo | Hạ tầng SNS/CloudWatch đã có | Chưa có người nhận SNS được xác nhận; cần sửa R05 và thử gửi/nhận thật. Email quản trị không tự động trở thành người nhận cảnh báo |
| Chi phí | Dự toán, hạn mức, mã đối soát chi phí theo môi trường | Đối soát thực tế chưa được chứng minh; số liệu có độ trễ và không tự tương đương hóa đơn sau credits. Hạn mức dự toán không phải giới hạn cứng của hóa đơn AWS |
| Hạ tầng | Sinh CloudFormation và lint nhiều mẫu/create/restore | Template hợp lệ về cấu trúc chưa chứng minh tạo/khôi phục/xóa thực tế thành công; T06 còn phụ thuộc image ECR/digest phù hợp |
| Kiểm thử | Bộ local khá rộng, có DOM và schema checks | Thiếu các ca chéo tính năng vừa tái hiện và bằng chứng end-to-end trên AWS |
| Bảo trì | Có phân chia module và tài liệu | Nhiều đoạn Python/JavaScript viết dồn một dòng, khó review và bàn giao cho nhóm 4 người; nên định dạng và tách các nhánh nghiệp vụ phức tạp |
| Báo cáo | Có đối chiếu yêu cầu và kế hoạch | Tổng công sức báo cáo gốc 800+80=880 giờ khác kế hoạch mới 500+60=560 giờ; cần giải thích chênh 320 giờ, không trình bày dự toán là giờ thực tế đã làm |

## Trạng thái AWS đã quan sát

Trong lần kiểm tra trực tiếp ngày 09/10/2026 tại ap-southeast-1:

- `cloudhub-network`: CREATE_COMPLETE.
- `cloudhub-demo`: ROLLBACK_COMPLETE, chưa phải triển khai thành công.
- Case AWS Support `179155110800156`: Unassigned, chưa thấy phản hồi của AWS trong case; liên quan xác minh tài khoản để tạo CloudFront.
- Chưa có URL public CloudHub đã được xác nhận hoạt động.

Đây là trạng thái tại thời điểm quan sát, không phải cam kết trạng thái hiện tại sẽ không thay đổi. Không suy ra tài khoản chắc chắn miễn phí từ việc mới đăng ký; cần kiểm tra credits, hạn dùng và tài nguyên còn giữ lại trước lần triển khai tiếp theo.

## Thứ tự hoàn thiện đề xuất

1. Sửa R01 và R06 trước: bảo vệ dữ liệu và cô lập quyền giữa người dùng. Sửa R02/R03 cùng đợt; chuyển các kịch bản tái hiện thành regression tests kiểm tra hành vi đúng.
2. Sửa R04/R05; rà soát mọi đường truy cập đặc quyền và mọi trạng thái cần người vận hành can thiệp.
3. Khi AWS xác minh xong, kiểm kê tài nguyên sau rollback, hoàn tất đầu vào triển khai và triển khai lại có kiểm soát chi phí.
4. Kiểm tra đăng nhập, quản trị, nhắc hết hạn và cảnh báo SNS đến người nhận thật. Việc đăng ký email SNS cần người nhận bấm link xác nhận của AWS.
5. Chạy 20 chu kỳ nghiệm thu đã đề ra, bao gồm tạo–ghi dữ liệu–backup–thu hồi–restore và các tình huống lỗi. Đo các chỉ tiêu phản hồi p95, trả mã tác vụ và thu hồi quá hạn bằng số liệu thật; không dùng kết quả mock để thay thế.
6. Kiểm tra UI trực tiếp trên desktop/mobile, đối soát tài nguyên/chi phí sau thu hồi, rồi cập nhật minh chứng và báo cáo bàn giao.

Đợt rà soát này tạo báo cáo và mã tái hiện, cập nhật kết quả kiểm thử local; chưa sửa các lỗi triển khai và không thay đổi tài nguyên AWS.
