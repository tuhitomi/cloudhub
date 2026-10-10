# CloudHub — bổ sung chức năng theo 10 điểm còn thiếu

Ngày cập nhật: 09/10/2026. Phiên bản nguồn/API: **0.4.0**.

Đã bổ sung giao diện, API, xử lý nền và cấu hình hạ tầng cho các chức năng dưới đây. **Chưa triển khai phiên bản này lên AWS.** Trạng thái AWS được ghi nhận trước đó vẫn là `cloudhub-demo: ROLLBACK_COMPLETE` do CloudFront yêu cầu xác minh tài khoản. Tổng vòng đời AWS thật: **0/20**; chưa có URL website công khai. Không kiểm tra lại Support hoặc tạo tài nguyên AWS trong lượt bổ sung này.

| Điểm yêu cầu | Phần đã bổ sung | Giới hạn / minh chứng còn thiếu |
|---|---|---|
| 1. Chạy AWS thật | Giữ quy trình triển khai và gate kiểm chứng cả T01–T10 | Tạm hoãn theo yêu cầu; chờ xác minh CloudFront. Không dùng local tests để mở gate |
| 2. SNS có người nhận | Tham số `AlertEmail`, subscription email tùy chọn; trang quản trị có trạng thái đăng ký và nút gửi email kiểm tra | Chưa chọn email nên để trống. Sau deploy phải bấm Confirm subscription, rồi nhận thư kiểm tra để chứng minh giao nhận |
| 3. Báo sắp hết hạn | Cảnh báo trong website khi còn 15 phút, cập nhật theo polling; sweep ghi nhận một lần mỗi deadline | Cần mở website và đăng nhập. Gia hạn làm cảnh báo cũ mất hiệu lực; chưa có email nhắc hết hạn |
| 4. Quản trị | Xem/tìm toàn bộ môi trường; thu hồi có lý do; xem/tạo tài khoản và thư mời; khóa/mở tài khoản; chỉnh hạn mức số môi trường và ngân sách từng người | Thu hồi vẫn sao lưu trước; chỉ xử lý nguồn có trạng thái cho phép và không giữ khóa. Không cấp nhóm admin qua giao diện |
| 5. Mật khẩu | Quên/đặt lại bằng mã email, đổi mật khẩu, xác minh email qua Cognito | Cần Cognito và email đã xác minh. Mật khẩu gửi trực tiếp tới Cognito, không qua CloudHub API hoặc lưu vào báo cáo |
| 6. Thử lại/hủy lỗi | Thử lại FAILED khi workflow kết thúc, trên môi trường cũ, tối đa 3 lần. “Đóng tác vụ lỗi” giữ nguồn/quota | Không dừng tác vụ đang chạy. CREATE/WAIT_CREATE cần kiểm tra/thu hồi trước khi tạo lại. Retry sao lưu đọc lại nguồn, tạo recovery point mới và đặt thêm dự toán |
| 7. Xóa/tải bản sao | Xóa có xác nhận; export/liên kết tải riêng tư tối đa 15 phút; khóa chống xóa/restore/export đồng thời | T02/T06/T07: ZIP dữ liệu S3. T09: JSON dữ liệu. T10: S3 và thông tin snapshot. T01/T03/T04/T05/T08: chỉ thông tin snapshot; lấy dữ liệu đầy đủ bằng khôi phục trên AWS |
| 8. Đối soát chi phí | Truy vấn từng môi trường theo tag `cloudhub:environment`, số ghi nhận/chênh dự toán; tối đa một yêu cầu/24 giờ/môi trường | Mặc định tắt để tránh phí truy vấn. Cần Cost Explorer và tag phân bổ. Số gross theo ngày UTC có độ trễ, chưa phân bổ hạ tầng chung/credits hoặc chốt hóa đơn. Thiếu dữ liệu là null, không tự ghi 0 |
| 9. Lịch sử/bộ lọc | Môi trường đã thu hồi, thời điểm, cleanup report và chi phí; tìm theo mã/mẫu/trạng thái; 100 tác vụ gần nhất | Đã kiểm thử local; chưa có lịch sử vòng đời AWS thật |
| 10. Hạn mức còn lại | Số môi trường/ngân sách dự toán trên trang và trước tạo; chặn nút khi thiếu; API kiểm tra trong giao dịch đặt chỗ | Là giới hạn dự toán của ứng dụng, không phải giới hạn cứng hóa đơn AWS |

## SNS dùng để làm gì?

SNS là dịch vụ chuyển thông báo của AWS. CloudWatch alarm của CloudHub gửi cảnh báo vận hành qua SNS khi sao lưu/tác vụ lỗi hoặc sweep thu hồi gặp sự cố. Email giúp người quản trị biết cần kiểm tra nguồn đang giữ lại hoặc tài nguyên chưa thu hồi, nhằm tránh mất dữ liệu và chi phí ngoài dự kiến.

Email nhận cảnh báo không được cấp quyền quản trị AWS. Có thể dùng email của nhóm hoặc email quản trị `24020341@vnu.edu.vn`, nhưng người dùng chưa xác nhận lựa chọn nên **chưa đăng ký email nào**. Sau khi AWS hoạt động: chọn email → deploy subscription → người nhận xác nhận email AWS → gửi email kiểm tra trong CloudHub → ghi minh chứng nhận thư. “Đã xác nhận đăng ký” hoặc Message ID không tự chứng minh thư tới hộp thư. [Hướng dẫn email SNS của AWS](https://docs.aws.amazon.com/us_en/sns/latest/dg/sns-email-notifications.html).

## Kiểm chứng đã thực hiện

`scripts/check.py` ghi kết quả vào `local-check-results.json`: **108 kiểm thử Python local/mock/SDK Stubber và 8 kiểm thử DOM với API giả lập**, không bỏ qua test; Python compile, cú pháp hai module frontend, OpenAPI và 21 nhóm cfn-lint đều đạt. Các tình huống rủi ro gồm chéo chủ sở hữu, tranh chấp export/delete/restore, export dữ liệu hỏng, quyền admin bị thu hồi, khóa tài khoản khi Cognito lỗi, không gửi trùng thư mời, workflow generation mới, số Decimal của DynamoDB và chi phí thiếu dữ liệu.

DOM tests dùng HTML/module thật, không phải kiểm thử browser/render hoặc AWS. Browser preview localhost trước đó không tới được server, nên **chưa xác minh bố cục trên trình duyệt**. Chưa có minh chứng Cognito gửi mã, SNS nhận thư, Cost Explorer trả dữ liệu hoặc snapshot/restore/export chạy thật. CI có cấu hình, chưa có CI run xác nhận.

## Các bước tiếp theo khi AWS mở quyền

1. Kiểm tra Support case `179155110800156`, quyền CloudFront và Free Plan/credits; giữ phạm vi đã duyệt 120 USD credits, 0 USD tiền mặt.
2. Kiểm kê stack rollback/resource Retain trước khi tạo lại; xử lý import/reuse hoặc tài nguyên trống bằng changeset phù hợp. Dùng network đã tạo, không tạo stack trùng.
3. Hoàn tất Docker/ECR digest được duyệt cho T06 và preflight. Điền `alert_email` sau khi chọn; giữ `actual_costs_enabled=false` cho tới khi review phí API/tag Billing.
4. Deploy control plane 0.4.0, lấy outputs, chạy `configure_frontend.py`, upload frontend gồm `portal.js` và `config.js` có region, tạo quản trị Cognito. Đăng nhập lại để nhận scope đổi mật khẩu.
5. Kiểm thử các luồng mới trên AWS, xác nhận SNS; chạy hai vòng tạo–ghi dữ liệu–backup–thu hồi–restore–audit từng mẫu. Chỉ mở template khi đủ minh chứng thật.

Mã chính: `backend/cloudhub/portal.py`, `portal_api.py`, `accounts.py`, `backup_export.py`, `actual_cost.py`, worker/runtime và `frontend/portal.js`. Hợp đồng API: `openapi.json`; cấu hình sinh từ `scripts/portal_infra.py` và `portal_openapi.py`.
