# CloudHub

Repository chỉ chứa file văn bản: mã nguồn, cấu hình mẫu, IaC, kiểm thử và tài liệu. Ảnh minh chứng, file nén, dependencies, credentials và cấu hình triển khai riêng được giữ ngoài Git. Một số đường dẫn minh chứng trong báo cáo cũ chỉ có ở workspace gốc.

**Đánh giá ngày 10/10/2026:** chưa sẵn sàng nghiệm thu hoặc mở dùng chung; sáu vấn đề về bảo vệ backup, phân quyền và cảnh báo còn chưa sửa. Xem [báo cáo rà soát](docs/review-report.md) và các tình huống tái hiện kèm theo.

Nguồn/API **0.4.0** bổ sung quản trị môi trường/tài khoản/hạn mức, cảnh báo sắp hết hạn, luồng mật khẩu, thử lại/đóng tác vụ lỗi, xóa/export bản sao, lịch sử/bộ lọc và đối soát chi phí tùy chọn. [Báo cáo 10 điểm và giới hạn thực tế](docs/product-completion.md). Phiên bản mới chưa deploy AWS.

Mã nguồn cho cả T01–T10 theo kiến trúc F00–F08/D01–D13, giao diện tiếng Việt, Cognito, HTTP API, Lambda, DynamoDB, Standard Step Functions, Scheduler, CloudFormation và bản sao theo mẫu. **Mạng AWS đã triển khai; control plane rollback vì AWS yêu cầu xác minh CloudFront. 0/20 vòng AWS thật.** Tất cả mẫu vẫn khóa với người dùng thường.

AWS CLI đã dùng phiên root tạm thời được người dùng duyệt; Free Plan còn 183,66 USD credits tại thời điểm đọc. Preflight đạt, `cloudhub-network` CREATE_COMPLETE, `cloudhub-demo` ROLLBACK_COMPLETE do CloudFront HTTP 403/account verification. Còn resource Retain đã kiểm kê; chưa có URL website. Chi tiết và bước tiếp tục trong báo cáo tổng hợp.

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
