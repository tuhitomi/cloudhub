"""Validate actual lifecycle evidence and propose gates. Does not update AWS."""
import argparse,json
from pathlib import Path
def review(record,account,region):
    if record.get('mode')!='AWS_REAL' or record.get('account_id')!=account or record.get('region')!=region or record.get('result')!='PASS':return False
    cycles=record.get('cycles',[])
    if len(cycles)<2 or record.get('completed_cycles',0)<2:return False
    ids=[c.get(key,{}).get('environment_id') for c in cycles for key in ['source_environment','restored_environment']]
    if any(not value for value in ids) or len(ids)!=len(set(ids)):return False
    for cycle in cycles:
        if cycle.get('result')!='PASS' or not cycle.get('recovery_point',{}).get('restore_verified'):return False
        for key in ['source_cleanup','restored_cleanup']:
            report=cycle.get(key,{})
            if not all(k in report for k in ['deleted','retained','residual','unknown']) or report['residual'] or report['unknown']:return False
        if not cycle.get('authorization'):return False
        if record['template_id'] in {'T01','T05'} and not cycle.get('ssh_access_verified'):return False
    return True
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--evidence-dir',required=True);p.add_argument('--out',required=True);args=p.parse_args()
    config=json.loads(Path(args.config).read_text(encoding='utf-8'));accepted=[];rejected=[]
    for tid in config['validation_templates']:
        path=Path(args.evidence_dir)/(tid+'.json')
        record=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        if record.get('template_id')==tid and review(record,config['account_id'],config['region']):accepted.append(tid)
        else:rejected.append(tid)
    Path(args.out).write_text(json.dumps({'proposed_verified_templates':accepted,'not_verified':rejected,'aws_gate_updated':False,
        'performance_and_expiry_require_separate_real_measurements':True},indent=2),encoding='utf-8')
    print('Review only:',','.join(accepted) or 'none');return 0 if not rejected else 1
if __name__=='__main__':raise SystemExit(main())
