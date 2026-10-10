# GitHub Pages: xem trước giao diện

Pages xuất bản giao diện tĩnh và 10 mẫu từ danh mục mã nguồn. Đây là bản xem trước, không phải minh chứng CloudHub đã vận hành trên AWS. Đăng nhập, hạn mức, dự toán, dữ liệu môi trường, sao lưu và mọi thao tác tạo/thu hồi vẫn bị khóa khi backend chưa triển khai.

Repo nguồn tiếp tục private. Workflow `Publish CloudHub UI preview` chỉ upload 6 file từ thư mục `site`: `index.html`, `style.css`, `app.js`, `config.js`, `catalog.json` và `.nojekyll`. File cấu hình luôn được tạo mới với `configured: false`, không lấy `frontend/config.js` trên máy. Không xuất bản backend, hạ tầng, evidence, khóa, tài khoản AWS hoặc thông tin người dùng.

```sh
python scripts/build_pages_preview.py --out site
```

Trong Settings → Pages, chọn Source → GitHub Actions. Push main khi frontend/danh mục/builder thay đổi sẽ cập nhật bản xem trước. Có thể chạy lại từ Actions → Publish CloudHub UI preview → Run workflow.

Muốn chuyển sang website hoạt động thật cần triển khai backend độc lập với CloudFront, cấu hình đúng Cognito callback/logout và API CORS cho địa chỉ Pages, rồi dùng một quy trình xuất bản giao diện thật riêng. Builder xem trước luôn giữ chức năng AWS bị khóa.
