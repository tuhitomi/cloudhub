"""Read an owned READY session until natural expiry; never calls manual destroy."""
import argparse,json,os,time,urllib.request
from pathlib import Path
from datetime import datetime,timezone
from access_token import TokenProvider
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--api',required=True);p.add_argument('--environment',required=True);p.add_argument('--out',required=True);args=p.parse_args()
    access=TokenProvider()
    def read():
        request=urllib.request.Request(args.api.rstrip('/')+'/environments/'+args.environment,headers={'Authorization':'Bearer '+access.get()})
        with urllib.request.urlopen(request,timeout=10) as response:return json.loads(response.read())
    env=read()
    if env['status']!='READY':raise SystemExit('Requires an owned READY session with natural deadline')
    deadline=env['deadline'];result={'mode':'LIVE_ENDPOINT','environment_id':args.environment,'deadline':deadline,'manual_destroy_called':False,'samples':[]}
    end=deadline+7500
    try:
        while time.time()<end:
            env=read();result['samples'].append({'at':time.time(),'status':env['status'],'expiry_triggered_at':env.get('expiry_triggered_at')})
            if env['deadline']!=deadline:raise RuntimeError('Session extended during measurement; use a fresh controlled run')
            if env.get('expiry_triggered_at'):
                delay=env['expiry_triggered_at']-deadline;result.update(trigger_delay_seconds=delay,trigger_target_met=0<=delay<120)
            if env['status'] in {'DELETED','RESIDUAL','ERROR_RETAINED','CREATE_FAILED'}:
                result.update(final_status=env['status'],cleanup_report=env.get('cleanup_report'),cleanup_finished_observed_at=time.time());break
            time.sleep(min(30,max(1,deadline-time.time())))
    finally:
        result['finished_at']=datetime.now(timezone.utc).isoformat();result.setdefault('trigger_target_met',False)
        Path(args.out).write_text(json.dumps(result,indent=2),encoding='utf-8')
    return 0 if result.get('trigger_target_met') and result.get('final_status')=='DELETED' else 1
if __name__=='__main__':raise SystemExit(main())
