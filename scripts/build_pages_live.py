"""Build the real browser client; publish only public assets and endpoint metadata."""
import argparse
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from generate_infra import frontend_urls

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_KEYS = {'configured', 'apiBase', 'clientId', 'cognitoDomain', 'redirectUri', 'region'}


def public_config(value):
    if set(value) != PUBLIC_KEYS or value.get('configured') is not True:
        raise ValueError('Config must contain exactly the six public connection fields')
    region = value['region']
    if not isinstance(region, str) or not re.fullmatch(r'[a-z]{2}-[a-z]+-\d', region):
        raise ValueError('Invalid AWS region')
    if not isinstance(value['clientId'], str) or not re.fullmatch(r'[a-z0-9]{1,128}', value['clientId']):
        raise ValueError('Invalid public Cognito client ID')
    for field, hostname in [
        ('apiBase', rf'[a-z0-9]+\.execute-api\.{re.escape(region)}\.amazonaws\.com'),
        ('cognitoDomain', rf'[a-z0-9-]+\.auth\.{re.escape(region)}\.amazoncognito\.com'),
    ]:
        parsed = urlsplit(value[field])
        if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port
                or not re.fullmatch(hostname, parsed.netloc) or parsed.path not in ('', '/')
                or parsed.query or parsed.fragment):
            raise ValueError('Invalid regional AWS HTTPS endpoint: ' + field)
    redirect, _ = frontend_urls(value['redirectUri'])
    return {**value, 'apiBase': value['apiBase'].rstrip('/'),
            'cognitoDomain': value['cognitoDomain'].rstrip('/'), 'redirectUri': redirect}


def build(destination, config):
    config = public_config(config)
    public = {name: (ROOT / 'frontend' / name).read_text(encoding='utf-8')
              for name in ['index.html', 'style.css', 'app.js', 'portal.js', 'auth.js']}
    public['config.js'] = 'window.CLOUDHUB = Object.freeze(' + json.dumps(config, ensure_ascii=False) + ');\n'
    public['.nojekyll'] = ''
    target = Path(destination).resolve()
    if target == ROOT or target == ROOT / 'frontend' or ROOT.is_relative_to(target):
        raise ValueError('Choose a dedicated live output directory')
    if target.exists():
        unexpected = [entry.name for entry in target.iterdir() if entry.name not in public or not entry.is_file()]
        if unexpected:
            raise ValueError('Output contains files outside the public allowlist: ' + ', '.join(unexpected))
    target.mkdir(parents=True, exist_ok=True)
    for name, content in public.items():
        (target / name).write_text(content, encoding='utf-8')
    return sorted(public)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    files = build(args.out, json.loads(args.config.read_text(encoding='utf-8')))
    print(json.dumps({'destination': str(args.out.resolve()), 'files': files, 'mode': 'LIVE_BACKEND_CLIENT'}))


if __name__ == '__main__':
    main()
