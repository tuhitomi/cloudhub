"""Run the approved real lifecycle harness sequentially; stop on first failure."""
import argparse,json,subprocess,sys
from pathlib import Path
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--api',required=True);p.add_argument('--config',required=True);p.add_argument('--out-dir',required=True)
    p.add_argument('--approved-create-resources',action='store_true');args=p.parse_args()
    if not args.approved_create_resources:raise SystemExit('Explicit creation approval required')
    config=json.loads(Path(args.config).read_text(encoding='utf-8'));results=[];out=Path(args.out_dir);out.mkdir(parents=True,exist_ok=True)
    for tid in config['validation_templates']:
        result=subprocess.run([sys.executable,str(Path(__file__).with_name('aws_lifecycle.py')),'--api',args.api,'--config',args.config,
            '--template',tid,'--cycles','2','--out',str(out/(tid+'.json')),'--approved-create-resources'])
        results.append({'template_id':tid,'exit_code':result.returncode})
        (out/'run-summary.json').write_text(json.dumps({'actual_commands':results,'complete':len(results)==10 and all(r['exit_code']==0 for r in results)},indent=2))
        if result.returncode:return result.returncode
    return 0
if __name__=='__main__':raise SystemExit(main())
