// Static preview only. No login, AWS request, credentials, or resource mutations.
const $ = selector => document.querySelector(selector);
const notice = 'Bản xem trước trên GitHub Pages. Xem 10 mẫu và cấu hình; đăng nhập, dự toán và quản lý môi trường sẽ mở khi backend AWS hoạt động.';
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
const specs = {
  T01: 'Máy Linux nhỏ · root 16 GiB · ổ dữ liệu 8 GiB',
  T02: 'Website tĩnh · tối đa 32 file, mỗi file 8 KiB',
  T03: 'Web và PostgreSQL · 32 bản ghi minh họa, mỗi bản ghi 256 byte',
  T04: 'Web có bộ cân bằng tải · từ 1 đến 2 máy · dùng chung PostgreSQL',
  T05: 'Website trên Linux · nội dung trong ổ dữ liệu 8 GiB',
  T06: 'Ứng dụng Docker đã duyệt · 1 tác vụ nhỏ · dữ liệu bền vững trong S3',
  T07: 'Kho file riêng tư · tối đa 32 file, mỗi file 8 KiB',
  T08: 'Hai máy Linux dùng chung EFS · tối đa 32 bản ghi nhỏ',
  T09: 'API và dữ liệu serverless · 32 bản ghi · chỉ chủ môi trường được truy cập',
  T10: 'Kho phân tích riêng tư · tối đa 32 bản ghi · giới hạn sử dụng 8 RPU-giờ/ngày',
};

function showTemplate(template) {
  $('#create-title').textContent = template.template_id + ' · ' + template.name;
  $('#configuration-description').textContent = specs[template.template_id];
  $('#create-limits').textContent = 'Hạn mức cá nhân sẽ hiển thị sau khi đăng nhập vào backend AWS.';
  $('#backup-description').textContent = 'Phạm vi sao lưu: ' + template.backup_scope + ' · Chính sách lưu bản sao 7 ngày.';
  $('#quote').replaceChildren(element('strong', 'Chưa có dự toán chi phí'), element('p', 'Dự toán cần giá đã duyệt và hạn mức thực tế từ backend. Bản xem trước không tạo hoặc thu hồi tài nguyên.'));
  $('#create-dialog').showModal();
}

async function start() {
  // Keep the shell usable even if the catalog cannot be downloaded.
  $('#notice').textContent = notice;
  $('#limits-summary').textContent = 'Chưa có dữ liệu hạn mức — backend AWS chưa kết nối.';
  for (const id of ['login', 'forgot-password', 'refresh', 'environment-search', 'environment-status', 'environment-template', 'hours', 'confirm-cost', 'confirm']) {
    const control = $('#' + id);
    control.disabled = true;
    control.title = 'Chức năng này cần backend AWS.';
  }
  $('#login').textContent = 'Đăng nhập · Chờ AWS';
  $('#confirm').textContent = 'Chờ backend AWS';
  $('#cancel').textContent = 'Đóng';
  $('#cancel').onclick = () => $('#create-dialog').close();
  $('#create-form').onsubmit = event => event.preventDefault();
  $('#environments').replaceChildren(element('p', 'Chưa kết nối backend. Môi trường đang chạy sẽ hiển thị tại đây sau khi đăng nhập.'));
  $('#environment-history').replaceChildren(element('p', 'Lịch sử sẽ hiển thị khi backend hoạt động.'));
  $('#backups').replaceChildren(element('p', 'Chưa có dữ liệu bản sao từ AWS.'));
  $('#operations').replaceChildren(element('p', 'Chưa có tác vụ thực tế để theo dõi.'));
  const response = await fetch(new URL('./catalog.json', import.meta.url));
  if (!response.ok) throw Error('Không tải được danh mục mẫu. Hãy tải lại trang.');
  const data = await response.json();
  if (data.preview !== true || !Array.isArray(data.items)) throw Error('Danh mục xem trước không hợp lệ.');
  $('#catalog').replaceChildren();
  for (const template of data.items) {
    const card = element('article', undefined, 'card');
    card.append(element('span', template.template_id, 'tag'), element('h3', template.name), element('p', template.purpose), element('small', template.services.join(' · ')), element('p', 'Xem trước · Chưa vận hành AWS', 'badge'));
    const details = element('button', 'Xem cấu hình');
    details.addEventListener('click', () => showTemplate(template));
    card.append(details);
    $('#catalog').append(card);
  }
}
start().catch(error => {$('#notice').textContent = error.message;});
