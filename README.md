# CloudHub

Repository chỉ chứa file văn bản: mã nguồn, cấu hình mẫu, IaC, kiểm thử và tài liệu. Ảnh minh chứng, file nén, dependencies, credentials và cấu hình triển khai riêng được giữ ngoài Git. Một số đường dẫn minh chứng trong báo cáo cũ chỉ có ở workspace gốc.

**Website AWS:** [CloudHub](https://d3qujq9kfq5gca.cloudfront.net/) đã trở lại kế hoạch gốc: S3 riêng tư + CloudFront/OAC, Cognito, HTTP API và xử lý tài nguyên tại ap-southeast-1, stack `cloudhub-pages-live` UPDATE_COMPLETE. AWS Support xác nhận tài khoản được duyệt ngày 12/10/2026. Đăng nhập API Cognito đã được người dùng phê duyệt và kiểm tra qua website thật; danh mục, hạn mức và quản trị hoạt động. Mẫu chỉ mở sau hai vòng đời AWS đạt yêu cầu; chưa nghiệm thu toàn bộ 10 mẫu và SLA.

Nguồn/API **0.4.2** có đăng nhập/refresh trực tiếp qua API Cognito để xử lý Hosted UI timeout, giữ nguyên pool/client, chữ ký JWT và kiểm tra owner/admin. Các chức năng 0.4.0–0.4.1 được giữ: quản trị môi trường/tài khoản/hạn mức, cảnh báo sắp hết hạn, luồng mật khẩu, thử lại/đóng tác vụ lỗi, xóa/export bản sao, lịch sử/bộ lọc và đối soát chi phí tùy chọn. Xem [thay đổi đăng nhập](docs/cognito-api-signin-review.md), [chức năng](docs/product-completion.md) và [bảo vệ dữ liệu](docs/security-fixes.md).

Mã nguồn cho cả T01–T10 theo kiến trúc F00–F08/D01–D13, giao diện tiếng Việt, Cognito, HTTP API, Lambda, DynamoDB, Standard Step Functions, Scheduler, CloudFormation và bản sao theo mẫu. D01 đã vận hành lại trên AWS; Pages chuyển người dùng đến URL CloudFront hiện tại. Gate AWS_VERIFIED của từng mẫu nằm ở backend.

AWS CLI dùng phiên tạm thời được người dùng duyệt, ngân sách 120 USD credits / 0 USD tiền mặt, không nâng Paid Plan. `cloudhub-network` vẫn CREATE_COMPLETE. Dữ liệu retained đã được kiểm kê và tái sử dụng. T07 và T09 đã đạt hai vòng và mở gate; các mẫu còn lại đang kiểm tra riêng. T06 cần image ECR đã quét/review, T10 đang bị Free Plan từ chối Redshift. SNS đã có đăng ký email được xác nhận và AWS đã nhận cảnh báo thử từ giao diện; chưa xác minh thư đến hộp thư. Cost Explorer chưa bật.

- [Trạng thái triển khai thật](docs/pages-live-status.md): mẫu đã mở, kết quả đo tải và các điều kiện còn thiếu.
- [Báo cáo tổng hợp](docs/progress.md): phần có mã, kết quả kiểm tra và điểm chặn.
- [Phiếu tài nguyên và dự toán](docs/deployment-review.md): khoảng 99,438 USD gross cho hai vòng mỗi mẫu; ngân sách 120 USD credits, tiền mặt 0 USD đã được duyệt. Billing thực tế có độ trễ.
- [Hướng dẫn triển khai và thu hồi](docs/deployment.md), [minh chứng/demo](docs/demo-evidence.md), [10 mẫu và phạm vi bản sao](docs/templates.md).
- [OpenAPI](docs/openapi.json), [hợp đồng bộ xử lý](docs/processor-contract.md), [đối chiếu A01–A25](docs/report-traceability.md), [kết quả local](docs/local-check-results.json).

## Chạy kiểm tra tại máy

Python 3.13 và Node.js 22; không cần AWS credentials để chạy các kiểm tra này:

```sh
python -m venv .venv
# Activate .venv for your operating system
python -m pip install -r requirements-dev.txt
npm ci
python scripts/generate_infra.py
python scripts/generate_network.py
python scripts/generate_openapi.py
python scripts/check.py
```

Để lập phiếu triển khai, tạo `config/local.json` từ `config/deployment.example.json` và `config/prices.local.json` từ `config/prices.example.json`, rồi điền thông tin và đơn giá đã kiểm tra. Chạy `python scripts/prepare_deployment.py --config config/local.json --prices config/prices.local.json --draft`. Các file có giá trị `REQUIRED` không phải cấu hình có thể triển khai ngay.

Kiểm thử local/mock và SDK Stubber được ghi riêng với AWS thật. Không lấy lint hoặc kết quả mock để mở gate AWS_VERIFIED. GitHub Actions được cấu hình để kiểm tra local, không triển khai AWS.

## Tổ chức mã

`frontend/`: giao diện, đăng nhập API Cognito và tương thích callback PKCE cũ; `backend/cloudhub/`: API/service/store, worker, routing và processor; `infra/`: SAM, workflow và network tùy chọn; `scripts/`: kiểm tra, review, preflight đọc, harness thật và đo; `tests/`: kiểm thử hành vi rủi ro. Giá và cấu hình review không chứa password/key/token. Các file local có bí mật, evidence riêng, PEM và config frontend đã triển khai bị loại khỏi gói source.
