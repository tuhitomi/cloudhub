"""Run local validation, record real results. AWS lifecycle evidence is separate."""
import contextlib
import compileall
import io
import json
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))

def main():
    checks=[]
    checks.append({'name':'python_compile','passed':all(compileall.compile_dir(str(ROOT/d),quiet=1) for d in ['backend','scripts','tests'])})
    stream=io.StringIO()
    with contextlib.redirect_stdout(stream):
        result=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.discover(str(ROOT/'tests')))
    checks.append({'name':'unit_tests_LOCAL_MOCK','passed':result.wasSuccessful() and not result.skipped,'count':result.testsRun,'skipped':len(result.skipped),'output':stream.getvalue()})
    for name in ['app.js','portal.js']:
        syntax=subprocess.run(['node','--check',str(ROOT/'frontend'/name)],text=True,capture_output=True)
        checks.append({'name':'frontend_syntax_'+name,'passed':syntax.returncode==0,'output':syntax.stdout+syntax.stderr})
    frontend=subprocess.run(['node','--test',str(ROOT/'tests/frontend.test.mjs')],cwd=ROOT,text=True,capture_output=True)
    checks.append({'name':'frontend_DOM_tests_LOCAL_MOCK','passed':frontend.returncode==0,'output':frontend.stdout+frontend.stderr})
    try:
        from openapi_spec_validator import validate
        validate(json.loads((ROOT/'docs/openapi.json').read_text(encoding='utf-8')))
        checks.append({'name':'openapi_schema','passed':True})
    except ImportError:
        checks.append({'name':'openapi_schema','passed':False,'blocked':'Install requirements-dev.txt'})
    except Exception as exc:
        checks.append({'name':'openapi_schema','passed':False,'output':str(exc)})
    try:
        from cfnlint.api import lint
        paths=[ROOT/'infra/control-plane.json']
        from cloudhub.templates import linux_template
        from cloudhub.s3_template import s3_template
        settings={'availability_zone':'us-east-1a','ami_id':'ami-0123456789abcdef0','vpc_id':'vpc-0123456789abcdef0','public_subnet_id':'subnet-0123456789abcdef0','instance_profile':'shared-ssm','ssh_cidr':'192.0.2.1/32'}
        sources=[('control_plane',paths[0].read_text()),('optional_shared_network',(ROOT/'infra/network.json').read_text())]
        for tid in ['T01','T05']:
            for snapshot in [None,'snap-0123456789abcdef0']:
                source=json.dumps(linux_template({'environment_id':'env-validation','owner_id':'validation','template_id':tid},settings,snapshot))
                sources.append((tid+('_restore' if snapshot else '_create'),source))
        sources.append(('T02_create_restore', json.dumps(s3_template({'environment_id':'env-'+'a'*24,'owner_id':'validation','template_id':'T02'}, {'account_id':'123456789012','region':'us-east-1'}))))
        from cloudhub.rds_template import rds_template
        from cloudhub.ecs_template import ecs_template
        from cloudhub.efs_template import efs_template
        from cloudhub.data_template import data_template
        from cloudhub.redshift_template import redshift_template
        settings.update(account_id='123456789012',region='us-east-1',public_subnet_ids=['subnet-0123456789abcdef0','subnet-abcdef01234567890'],
            db_subnet_ids=['subnet-0123456789abcdef0','subnet-abcdef01234567890'],permissions_boundary='arn:aws:iam::123456789012:policy/boundary',
            postgres_version='16.3',cognito_client_id='clientid',cognito_issuer='https://cognito-idp.us-east-1.amazonaws.com/us-east-1_abcd',
            management_table='cloudhub-meta',management_table_arn='arn:aws:dynamodb:us-east-1:123456789012:table/cloudhub-meta',
            frontend_origin='https://demo.cloudfront.net',approved_image='123456789012.dkr.ecr.us-east-1.amazonaws.com/cloudhub-demo@sha256:'+'a'*64)
        for tid,builder in [('T03',rds_template),('T04',rds_template),('T06',ecs_template),('T07',s3_template),('T08',efs_template),('T09',data_template),('T10',redshift_template)]:
            for recovery in [None,{'db_snapshot_id':'cloudhub-rp-'+'b'*24,'redshift_snapshot_name':'cloudhub-rp-'+'b'*24}]:
                env={'environment_id':'env-'+'a'*24,'owner_id':'validation','template_id':tid}
                source=builder(env,settings,recovery) if tid!='T07' else builder(env,settings)
                sources.append((tid+('_restore' if recovery else '_create'),json.dumps(source)))
        for name,source in sources:
            errors=lint(source,regions=['us-east-1'])
            blocking=[e for e in errors if e.rule.id.startswith('E')]
            checks.append({'name':'cfn_lint_'+name,'passed':not blocking,'output':[str(e) for e in errors]})
    except ImportError:
        checks.append({'name':'cfn_lint','passed':False,'blocked':'Install requirements-dev.txt'})
    except Exception as exc:
        checks.append({'name':'cfn_lint','passed':False,'output':repr(exc)})
    output={'recorded_at':datetime.now(timezone.utc).isoformat(),'mode':'LOCAL_ONLY','aws_lifecycle_cycles':0,
            'aws_metrics_measured':False,'checks':checks}
    (ROOT/'docs/local-check-results.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    for c in checks:print(('PASS ' if c['passed'] else 'FAIL ')+c['name'],c.get('output','') if not c['passed'] else '')
    return 0 if all(c['passed'] for c in checks) else 1

if __name__=='__main__':raise SystemExit(main())
