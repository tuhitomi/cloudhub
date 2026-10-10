# GitHub Pages: giao diện và backend riêng

Pages xuất bản giao diện tĩnh và 10 mẫu từ danh mục mã nguồn. Đây là bản xem trước, không phải minh chứng CloudHub đã vận hành trên AWS. Đăng nhập, hạn mức, dự toán, dữ liệu môi trường, sao lưu và mọi thao tác tạo/thu hồi vẫn bị khóa khi backend chưa triển khai.

Repo nguồn tiếp tục private. Workflow `Publish CloudHub website` có hai chế độ. Khi chưa có `config/pages-public.json`, workflow giữ bản xem trước: chỉ upload `index.html`, `style.css`, `app.js`, `config.js`, `catalog.json` và `.nojekyll`; cấu hình được tạo mới với `configured: false`. Không lấy `frontend/config.js` trên máy.

```sh
python scripts/build_pages_preview.py --out site
```

Trong Settings → Pages, chọn Source → GitHub Actions. Push main khi frontend/danh mục/builder hoặc cấu hình công khai thay đổi sẽ cập nhật website. Có thể chạy lại từ Actions → Publish CloudHub website → Run workflow.

Để kết nối backend thật, tạo template riêng. Lệnh này giữ template CloudFront mặc định và không tạo bucket/CDN cho giao diện. Cognito callback/logout giữ đường dẫn `/cloudhub/`; CORS của API và T09 chỉ nhận origin `https://tuhitomi.github.io`.

```sh
python scripts/generate_infra.py --frontend-url https://tuhitomi.github.io/cloudhub/ --out /absolute/path/control-plane-pages.json
```

Triển khai backend bằng stack riêng sau kiểm kê tài nguyên retained của lần thất bại, kiểm Free Plan/credits, giá và changeset. Các quyền quản lý CloudFront của mẫu T02 vẫn có trong vai trò mẫu; bỏ CDN của giao diện không chứng minh tài khoản đã được xác minh hoặc T02 chạy được.

Sau khi stack CREATE_COMPLETE hoặc UPDATE_COMPLETE, lấy outputs thật vào file local và tạo cấu hình công khai:

```sh
python scripts/configure_frontend.py /absolute/path/stack-outputs.json --out-json config/pages-public.json
python scripts/build_pages_live.py --config config/pages-public.json --out /absolute/path/pages-live
```

Chỉ commit `pages-public.json` gồm sáu trường được kiểm tra: configured, apiBase, clientId, cognitoDomain, redirectUri, region. Đây là metadata endpoint và client Cognito không có secret; không thêm user, mật khẩu, access key, token, outputs đầy đủ hoặc evidence. Builder từ chối trường khác, endpoint sai region và thư mục có file ngoài allowlist. Bản hoạt động chỉ xuất bản `index.html`, `style.css`, `app.js`, `portal.js`, `config.js`, `.nojekyll`; binary/backend vẫn nằm ngoài repository và Pages.

Backend tiếp tục xác thực JWT, kiểm owner/admin, quota, dự toán và gate mẫu. `verified_templates=[]` cho đến khi có bằng chứng vòng đời AWS thật. Không mở nút tạo chỉ để minh họa. T06 cần image ECR được review; T10 cần kiểm plan/quota riêng. SNS cần người nhận xác nhận; Cost Explorer chỉ bật sau review chi phí. Khi cần trở lại bản xem trước, bỏ cấu hình công khai trong một commit rồi chạy workflow.
