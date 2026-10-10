"""Build a public, static UI preview using a strict file allowlist.

Never copy deployed config.js, credentials, AWS evidence, or the backend.
The catalog describes source configurations, not running AWS resources.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def build(destination):
    spec = importlib.util.spec_from_file_location("preview_catalog", ROOT / "backend/cloudhub/catalog.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    items = module.catalog(verified=[])
    html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    html = html.replace("CloudHub • Môi trường học tập", "CloudHub • Xem trước giao diện")
    html = html.replace('class="brand" href="/"', 'class="brand" href="./"')
    # Remove credential and mutation forms entirely from the public preview.
    html = re.sub(r'<dialog id="(?:files|records|password|account|action)-dialog">.*?</dialog>', '', html, flags=re.S)
    html = re.sub(r'<section id="admin".*?</section>', '', html, flags=re.S)
    html = html.replace('<button id="forgot-password">', '<button id="forgot-password" disabled>')
    html = html.replace('<button id="login">', '<button id="login" disabled>')
    html = html.replace('TỪ Ý TƯỞNG ĐẾN MÔI TRƯỜNG CLOUD', 'BẢN XEM TRƯỚC · BACKEND ĐANG CHỜ TRIỂN KHAI')
    html = html.replace('Đang tải cấu hình…', 'Bản xem trước giao diện. Đăng nhập và tạo môi trường sẽ mở sau khi kết nối backend AWS.')
    html = re.sub(r'<footer>.*?</footer>', '<footer>CloudHub · Bản xem trước giao diện · Chưa kết nối backend AWS · Không tạo tài nguyên cloud.</footer>', html, flags=re.S)
    public = {
        "index.html": html,
        "style.css": (ROOT / "frontend/style.css").read_text(encoding="utf-8"),
        "app.js": (ROOT / "frontend/pages-preview.js").read_text(encoding="utf-8"),
        "config.js": 'window.CLOUDHUB = Object.freeze({configured: false, preview: true});\n',
        "catalog.json": json.dumps({"preview": True, "items": items}, ensure_ascii=False, indent=2) + "\n",
        ".nojekyll": "",
    }
    destination = Path(destination).resolve()
    if destination.exists():
        unexpected = sorted(entry.name for entry in destination.iterdir() if entry.name not in public or not entry.is_file())
        if unexpected:
            raise ValueError("Preview directory contains files outside the public allowlist: " + ", ".join(unexpected))
    destination.mkdir(parents=True, exist_ok=True)
    for name, content in public.items():
        (destination / name).write_text(content, encoding="utf-8")
    print(json.dumps({"destination": str(destination), "files": sorted(public), "templates": len(items), "backend_connected": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="Use a dedicated output directory, never the source frontend directory.")
    args = parser.parse_args()
    target = Path(args.out).resolve()
    if target == ROOT or target == ROOT / "frontend" or ROOT.is_relative_to(target):
        parser.error("Choose a dedicated preview output directory.")
    build(target)
