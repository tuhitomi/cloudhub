# Hai chế độ vận hành và dự toán CloudHub 0.5

CloudHub giúp người chưa biết AWS tạo và quản lý hạ tầng theo nhu cầu, có dự toán trước khi xác nhận. Thời hạn là một lựa chọn quản lý. Luồng tạo: nhu cầu → cấu hình chuẩn đã kiểm chứng → chế độ/chính sách → dự toán → xác nhận → sử dụng và quản lý.

## Chế độ và phạm vi

`AUTO_OFF` là mặc định. `AUTO_RENEW` chỉ được bật bằng lựa chọn và xác nhận của người dùng, cùng chu kỳ 5–480 phút và giới hạn dự toán cộng dồn. Khi đến deadline, backend đọc lại trạng thái, khóa, deadline, revision, chế độ, hạn mức người dùng/toàn hệ thống, đơn giá đã duyệt và tổng dự toán đã cam kết. Gia hạn chỉ cập nhật metadata/deadline và lịch; không gọi create, stop, restart hoặc delete của bộ xử lý mẫu. Khi không đủ điều kiện, lưu và thông báo lý do rồi áp dụng chính sách kết thúc đã xác nhận.

`PAUSE` chỉ hỗ trợ T01/T05: dừng chính EC2 hiện có, giữ root/data EBS, key, security group và stack; không xóa dữ liệu. Compute ngừng khi AWS xác nhận stopped; EBS, bản sao và log còn lưu có thể tiếp tục có phí. Môi trường tạm dừng vẫn chiếm quota. Mở lại dùng cùng instance/volume; IP có thể đổi. Khi thu hồi một máy đã tạm dừng, hệ thống có thể phải mở lại để kiểm dữ liệu và tạo bản sao cuối.

`BACKUP_RETIRE` hỗ trợ các mẫu hiện có: chỉ vào DELETE sau khi bản sao bắt buộc AVAILABLE, thuộc đúng nguồn/chủ sở hữu và được khóa bảo vệ. Sao lưu thất bại giữ nguồn, cố khôi phục hoạt động, báo quản trị; tối đa hai lần thử lại tự động sau lần đầu và khi workflow trước đã kết thúc. Mỗi lần dùng generation/recovery point mới, khóa cũ giữ đến khi thu hồi sạch. Retry không có đủ dự toán thì cũng giữ nguồn; không bỏ qua backup. Retry thủ công vẫn có giới hạn riêng. T10 giữ gate khóa, chưa được phép tạo.

Kết quả tạm dừng và thu hồi phân biệt trên giao diện: tài nguyên đã xóa, giữ có chủ đích, còn sót, chưa xác minh và phần còn có phí. Quota chỉ trả sau audit DELETED sạch; tạm dừng không trả quota. Báo cáo audit chưa xác minh sạch không được đổi thành PASS.

## Cách tính chi phí

`estimate_hours` (1–8 giờ) là khoảng xem dự toán, không đặt lịch tắt. `operating_policy.period_minutes` đặt kỳ vận hành từ khi dữ liệu sẵn sàng. Giao diện hiển thị riêng khoảng xem, kỳ đầu, từng kỳ gia hạn và giữ dữ liệu sau kết thúc. Kỳ gia hạn bỏ dự phòng dựng mới và bản sao cuối của kỳ đầu; lưu lượng/request/log/điều khiển là một phong bì sử dụng dự toán riêng mỗi kỳ. Đơn giá, region, thời điểm cập nhật, giả định và khoản chưa đo được hiển thị trong chi tiết.

Ledger `estimated_committed_milliusd` cộng kỳ đầu, mọi gia hạn tự động/thủ công, mở lại và dự phòng lưu dữ liệu; bản sao thêm/retry thêm cũng cộng vào ledger. Không đặt lại ledger khi đổi chế độ hoặc tăng giới hạn. Dự phòng kết thúc ban đầu gồm 7 ngày bản sao khi thu hồi hoặc 24 giờ EBS khi tạm dừng. Đổi sang chính sách giữ dữ liệu chưa dự phòng trước đó cần cộng khoản bổ sung, không hoàn lại khoản dự phòng cũ. `Q#<sub>` và `Q#GLOBAL` giữ tổng dự toán được cam kết, trong cùng transaction với deadline/quyết định kết thúc. Thu hồi sạch chỉ trả số lượng môi trường active; `reserved_milliusd` vẫn cộng dồn các môi trường trước đó, không tự hoàn lại để tạo lại vô hạn. Phạm vi ngân sách người dùng/toàn hệ thống là từ lúc khởi tạo ledger đến khi quản trị có thay đổi được xác nhận; phạm vi cap AUTO_RENEW là toàn vòng đời của một environment_id.

Giới hạn dùng để quyết định tiếp tục gia hạn; không bảo đảm hóa đơn AWS thực tế. Phí EBS sau 24 giờ tạm dừng cho đến khi thu hồi, dữ liệu lớn hơn giả định, log/bản sao cũ/hạ tầng dùng chung, xử lý trễ và lưu lượng chưa đo có thể vượt phạm vi dự toán. Không kiểm từng kỳ độc lập để cho phép gia hạn vô hạn: mỗi kỳ phải làm tăng tổng cam kết và còn trong giới hạn môi trường/người dùng/toàn hệ thống. Chi phí chỉ là dự toán gross có dự phòng, không phải Billing/Cost Explorer đã đối soát. Đơn giá SSM được làm mới tối đa mỗi 60 giây; không lấy đơn giá cũ làm được duyệt khi lần đọc mới thất bại.

## Khóa, lịch và khôi phục

Mỗi lần đổi chính sách/deadline/kết thúc tăng `lifecycle_revision`, cùng optimistic version của DynamoDB. Tác vụ dài trả 202 và `operation_id`. API lấy chủ sở hữu từ JWT Cognito đã xác minh; không nhận owner/resource IDs từ biểu mẫu. Cùng Idempotency-Key trả cùng operation và không cộng chi phí/gia hạn lại. Tại thời điểm tranh chấp, một transaction lấy khóa trước; yêu cầu khác phải tải lại. Không chen gia hạn hoặc đổi mode sau deadline khi tiến trình kết thúc đã thắng.

Mỗi môi trường dùng một schedule ổn định `<env-id>-lifecycle`, payload gồm deadline/revision mong đợi. Callback cũ bỏ qua khi deadline/revision đổi; callback luôn đọc lại chế độ/trạng thái hiện hành. Callback đời cũ không có revision chỉ còn được nhận cho môi trường revision 0 chưa thay đổi; sau khi môi trường có revision mới phải dùng callback có fence hoặc sweep đọc lại hiện trạng. Không tự bật gia hạn. Đặt `ActionAfterCompletion=NONE` cho lịch ổn định để một lần giao cũ không xóa lịch kỳ mới; hủy lịch sau tạm dừng/thu hồi.

| Dữ liệu bổ sung trên E# / O# | Ý nghĩa |
|---|---|
| operating_schema, operating_policy | Schema 2; chế độ, chu kỳ, cap, chính sách kết thúc và xác nhận |
| lifecycle_revision / target_revision | Fence deadline/chính sách, tách khỏi optimistic version của row |
| estimate_hours, cost_plan, first_period_estimate, last_period_estimate | Khoảng xem, kỳ đầu, kỳ tiếp theo và giữ dữ liệu |
| estimated_committed_milliusd, renewal_count, last_renewal | Tổng cam kết tăng dần và lịch sử gia hạn |
| schedule_pending, schedule_sync_operation_id, schedule_repair_sequence | Outbox đồng bộ/khôi phục lịch không tính lặp |
| retirement_allowance_committed, pause_holding_allowance_committed | Không tính lặp dự phòng giữ dữ liệu đã được cam kết |
| renewal_blocked_reason, pause_report / cleanup_report | Lý do không gia hạn; đã xóa/giữ/còn sót/chưa xác minh/còn phí |

Theo [AWS UpdateSchedule](https://docs.aws.amazon.com/scheduler/latest/APIReference/API_UpdateSchedule.html), trường tùy chọn bị bỏ qua sẽ trở về mặc định. Bộ đồng bộ gọi GetSchedule, kiểm target/role/environment, giữ description/KMS/start/end/target/retry/DLQ đầy đủ rồi cập nhật mốc và payload. ClientToken dựa trên deadline/revision/lượt sửa; retry cùng nội dung không tạo lịch mới. Quyền Get/Update/Delete/Create chỉ trên `env-*` trong schedule group của project, giữ nguyên quyền PassRole hiện có.

Nếu deadline đã commit nhưng SDK lịch lỗi, giữ deadline và nguồn, lưu `schedule_pending` cùng operation có thể retry. Sweep không cập nhật lịch từ một snapshot không khóa; nó tạo hoặc thử lại operation đồng bộ có khóa. Khi đã qua mốc trong lúc sửa lịch, xử lý deadline hiện hành trực tiếp, không giả rằng một lịch quá khứ đã đồng bộ. Retry lịch không cộng tiền/gia hạn lại. Sau hai lần tự thử lại vẫn lỗi, giữ cờ cần quản trị/thử lại thủ công.

## Chuyển đổi và kiểm chứng

Không chạy migration hàng loạt hay sửa/xóa bảng. Môi trường thiếu chính sách được đọc như `AUTO_OFF/BACKUP_RETIRE`, chu kỳ từ `duration_hours` cũ và revision 0. Chỉ khi người dùng thay đổi có xác nhận mới lưu schema 2/chính sách mới. Input `duration_hours` cũ vẫn tương thích với tự động sao lưu/thu hồi; `/extend` chuyển sang operation 202 dùng cơ chế gia hạn chung.

Kiểm thử LOCAL/mock/SDK/DOM bao gồm ngân sách cộng dồn, quota giảm, transaction race giữa từ chối và cập nhật quota, callback cũ sau khi deadline đổi, duplicate/retry, thay đổi sát hạn, SDK lịch lỗi và sửa không tính lặp, backup thất bại và giới hạn retry, tạm dừng/mở lại/thu hồi, truy cập chéo và bảo toàn cấu hình UpdateSchedule. Những kết quả này không chứng minh AWS thật. Minh chứng AWS mới phải ghi riêng ID, timestamp, trạng thái, checksum và audit thực tế; không dùng 18 vòng cũ để nghiệm thu chế độ mới.

[AWS: dừng/khởi động EC2](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/Stop_Start.html) và [kiểu lịch một lần](https://docs.aws.amazon.com/scheduler/latest/UserGuide/schedule-types.html) là tài liệu tham chiếu cho các giới hạn vận hành.

## Kết quả ngày 13/10/2026

**LOCAL:** 223 ca Python và 31 ca DOM PASS, không skip; syntax, OpenAPI và CloudFormation lint PASS. Kiểm thử HTTP localhost/temp cần chạy ngoài sandbox hạn chế của Windows. Các ca quota giảm, transaction race, backup lỗi và SDK lịch lỗi là mock/SDK stub, không phải sự cố đã tạo trên AWS.

**AWS thật:** cập nhật tại chỗ `cloudhub-pages-live` UPDATE_COMPLETE, không thay pool/client/bảng/nguồn dữ liệu; 7/7 asset CloudFront khớp SHA256, S3 trực tiếp trả 403, giữ gate T01–T09 và concurrency Lambda 10. Một T05 có kỳ xem 8 giờ, vận hành 5 phút được kiểm qua deadline tự nhiên: AUTO_RENEW giữ instance/volume/stack/checksum, cập nhật deadline/lịch; cap cộng dồn 0,805 USD từ chối kỳ tiếp theo và PAUSE giữ dữ liệu/quota. Đổi AUTO_OFF, mở lại cùng nguồn và checksum, duplicate không mở lại/tính lặp. Sau đó deadline AUTO_OFF cũng tạm dừng; thu hồi chủ động cần mở lại nguồn để sao lưu.

Nguồn cuối DELETED, 7 tài nguyên được xác minh đã xóa, residual/unknown rỗng; một snapshot cuối AVAILABLE được giữ 7 ngày. Chỉ quota số lượng được trả; tổng cam kết môi trường/người dùng 1,267 USD vẫn giữ trong ledger, là dự toán gross, chưa đối soát hóa đơn. Hai tài khoản thử riêng đã khóa, API từ chối token cũ của chúng, file mật khẩu riêng đã xóa; tài khoản của người dùng và lịch sử/bản sao được giữ.

AWS đã giúp phát hiện lỗi dự toán mặc định đọc chu kỳ `Decimal` từ DynamoDB; đã sửa, thêm regression test và kiểm lại dự toán/thu hồi thật. Công cụ minh chứng cũng sửa so sánh tuple/list khi đọc JSON, tiếp tục cùng nguồn, không sửa deadline để giả PASS. Callback cũ được phát lại qua Expiry Lambda thật và bỏ qua; không coi phát lại đó là Scheduler delivery tự nhiên. Chưa độc lập đối chiếu nguồn của mọi invocation với fallback, chưa restore lại snapshot cuối, chưa thử mọi tổ hợp chế độ của chín mẫu hoặc chứng minh SLA. Xem [minh chứng đã lược bỏ dữ liệu riêng](operating-modes-validation.json).

Email Free Tier ngày 13/10 báo hết 4.000 lượt chuyển trạng thái miễn phí của Step Functions trong tháng, không phải hết credits. Account được đọc là FREE/ACTIVE, còn 182,23 USD credits tại thời điểm kiểm tra; billing có độ trễ. [AWS Free Tier FAQ](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-FAQ.html) giải thích phần vượt allowance dùng credits và Free Plan không tính tiền mặt khi vẫn giữ plan này. Không nâng Paid Plan, không mở AWS Support hoặc tăng quota trong thay đổi này.
