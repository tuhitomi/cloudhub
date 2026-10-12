"""Keep the former Pages bookmark pointing to the restored AWS portal."""
import argparse,html,json,re
from pathlib import Path

def build(out,config):
    url=config['frontend_url']
    if not re.fullmatch(r'https://d[a-z0-9]+\.cloudfront\.net/',url):
        raise ValueError('Redirect must be the published native CloudFront root')
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    safe=html.escape(url,quote=True)
    (out/'index.html').write_text('<!doctype html><html lang="vi"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>CloudHub trên AWS</title><meta http-equiv="refresh" content="0;url='+safe+'">'
        '<h1>CloudHub đã chuyển về AWS</h1><p><a href="'+safe+'">Mở website CloudHub</a></p></html>',encoding='utf-8')
    (out/'.nojekyll').write_text('',encoding='utf-8')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True);parser.add_argument('--out',required=True);args=parser.parse_args()
    build(args.out,json.loads(Path(args.config).read_text(encoding='utf-8')))
