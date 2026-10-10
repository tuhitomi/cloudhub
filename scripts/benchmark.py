"""Read-only live management API benchmark; never substitutes local metrics for AWS."""
import argparse
import base64
import concurrent.futures
import json
import math
import os
import time
import urllib.request
from datetime import datetime,timezone
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--api',required=True);p.add_argument('--out',required=True);p.add_argument('--requests',type=int,default=200);args=p.parse_args()
tokens=json.loads(os.environ['CLOUDHUB_BENCH_TOKENS']) if os.getenv('CLOUDHUB_BENCH_TOKENS') else [os.environ['CLOUDHUB_ACCESS_TOKEN']]
if not isinstance(tokens,list) or not tokens or not all(isinstance(t,str) and t for t in tokens):raise SystemExit('Provide a JSON array of Cognito access tokens')
identities=set()
for token in tokens:
    payload=token.split('.')[1];claims=json.loads(base64.urlsafe_b64decode(payload+'='*((4-len(payload)%4)%4)))
    identities.add((claims['iss'],claims['sub']))
if args.requests<20:raise SystemExit('Need at least 20 requests for the concurrency benchmark')
def read(i):
    start=time.perf_counter()
    request=urllib.request.Request(args.api.rstrip('/')+'/environments',headers={'Authorization':'Bearer '+tokens[i%len(tokens)]})
    try:
        with urllib.request.urlopen(request,timeout=10) as response: response.read();status=response.status
    except Exception as exc:status=type(exc).__name__
    return {'ms':(time.perf_counter()-start)*1000,'status':status}
with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:results=list(pool.map(read,range(args.requests)))
latencies=sorted(r['ms'] for r in results);p95=latencies[math.ceil(len(latencies)*.95)-1]
out={'recorded_at':datetime.now(timezone.utc).isoformat(),'mode':'LIVE_ENDPOINT','concurrency':20,'distinct_authenticated_subjects':len(identities),
    'twenty_user_target_measured':len(identities)>=20,'requests':results,'p95_ms':p95,'read_target_met':len(identities)>=20 and p95<2000 and all(r['status']==200 for r in results),
    'long_command_target':'NOT_MEASURED','scheduler_target':'NOT_MEASURED','aws_authentication_required':True}
Path(args.out).write_text(json.dumps(out,indent=2),encoding='utf-8');print('p95:',round(p95,1),'ms; target met:',out['read_target_met'])
