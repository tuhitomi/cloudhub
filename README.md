# CloudHub

Repository chỉ chứa file văn bản: mã nguồn, cấu hình mẫu, IaC, kiểm thử và tài liệu. Ảnh minh chứng, file nén, dependencies, credentials và cấu hình triển khai riêng được giữ ngoài Git. Một số đường dẫn minh chứng trong báo cáo cũ chỉ có ở workspace gốc.

**Website hoạt động:** [CloudHub](https://tuhitomi.github.io/cloudhub/) dùng GitHub Pages cho giao diện và stack AWS `cloudhub-pages-live` cho Cognito, API và xử lý tài nguyên tại ap-southeast-1. Backend đã CREATE_COMPLETE rồi UPDATE_COMPLETE; đã xác minh đăng nhập, hạn mức và màn hình quản trị qua website thật. Các mẫu được mở dần sau kiểm thử vòng đời; chưa đủ minh chứng nghiệm thu toàn bộ 10 mẫu và SLA.

Nguồn/API **0.4.1** giữ các chức năng đã bổ sung ở 0.4.0: quản trị môi trường/tài khoản/hạn mức, cảnh báo sắp hết hạn, luồng mật khẩu, thử lại/đóng tác vụ lỗi, xóa/export bản sao, lịch sử/bộ lọc và đối soát chi phí tùy chọn. Xem [báo cáo chức năng](docs/product-completion.md), [sáu bản sửa bảo vệ dữ liệu](docs/security-fixes.md) và [kết nối Pages với AWS](docs/github-pages.md).

Mã nguồn cho cả T01–T10 theo kiến trúc F00–F08/D01–D13, giao diện tiếng Việt, Cognito, HTTP API, Lambda, DynamoDB, Standard Step Functions, Scheduler, CloudFormation và bản sao theo mẫu. D01 dùng Pages làm phương án dự phòng đã được người dùng chọn; template CloudFront gốc vẫn được giữ. Gate AWS_VERIFIED của từng mẫu nằm ở backend.

AWS CLI dùng phiên tạm thời được người dùng duyệt, ngân sách 120 USD credits / 0 USD tiền mặt, không nâng Paid Plan. `cloudhub-network` vẫn CREATE_COMPLETE. Các stack thất bại cũ được giữ và dữ liệu retained đã kiểm kê để tái sử dụng; backend mới không phụ thuộc CDN CloudFront của giao diện. T02 vẫn cần quyền tạo CloudFront, T06 cần image ECR được duyệt, T10 cần kiểm plan/quota riêng. SNS đã có đăng ký email được xác nhận và AWS đã nhận yêu cầu cảnh báo thử từ giao diện; chưa xác minh thư đến hộp thư. Cost Explorer chưa bật.

- [Trạng thái triển khai thật](docs/pages-live-status.md): T07 đã mở, kết quả đo tải 20 người dùng và các điều kiện còn thiếu.
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

`frontend/`: giao diện và Cognito PKCE; `backend/cloudhub/`: API/service/store, worker, routing và processor; `infra/`: SAM, workflow và network tùy chọn; `scripts/`: kiểm tra, review, preflight đọc, harness thật và đo; `tests/`: kiểm thử hành vi rủi ro. Giá và cấu hình review không chứa password/key/token. Các file local có bí mật, evidence riêng, PEM và config frontend đã triển khai bị loại khỏi gói source.
