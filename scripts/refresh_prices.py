"""Refresh the reviewed region's selected official price rows, leaving approval false."""
import argparse,csv,io,json,urllib.request
from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal
def refresh(source):
    out=json.loads(json.dumps(source));groups={}
    for key,provenance in out['provenance'].items():groups.setdefault(provenance['source_url'],[]).append(key)
    for url,keys in groups.items():
        wanted={out['provenance'][key]['rate_code']:key for key in keys};found=set()
        with urllib.request.urlopen(url,timeout=60) as response:
            text=io.TextIOWrapper(response,encoding='utf-8-sig');metadata=[text.readline().strip() for _ in range(5)]
            for row in csv.DictReader(text):
                key=wanted.get(row.get('RateCode'))
                if key is None:continue
                old=out['provenance'][key]
                if row['Currency']!='USD' or row['TermType']!='OnDemand' or row['SKU']!=old['sku'] or row['Unit']!=old['aws_unit']:raise ValueError('Selection changed; review manually: '+key)
                old.update(publication=metadata[2],aws_price_per_unit=row['PricePerUnit'])
                out['units'][key]=str(Decimal(row['PricePerUnit'])*old['multiplier']);found.add(key)
        if found!=set(keys):raise ValueError('Selected rates missing; review new SKU/tier before deployment: '+','.join(sorted(set(keys)-found)))
    out.update(approved=False,updated_at=datetime.now(timezone.utc).isoformat());return out
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True);p.add_argument('--out',required=True);args=p.parse_args()
    result=refresh(json.loads(Path(args.source).read_text(encoding='utf-8')))
    Path(args.out).write_text(json.dumps(result,indent=2),encoding='utf-8');print('Prices refreshed; approval remains false.')
if __name__=='__main__':main()
