"""Read stack outputs copied from AWS; write public Cognito/API config only."""
import json
import argparse
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('stack_outputs');args=p.parse_args()
data=json.loads(Path(args.stack_outputs).read_text(encoding='utf-8'))
outputs=data['Stacks'][0]['Outputs'] if 'Stacks' in data else data
values={o['OutputKey']:o['OutputValue'] for o in outputs}
cfg={'configured':True,'apiBase':values['ApiUrl'],'clientId':values['ClientId'],'cognitoDomain':values['CognitoDomain'],'redirectUri':values['FrontendUrl']+'/'}
cfg['region']=values['Region']
dest=Path(__file__).resolve().parents[1]/'frontend/config.js'
dest.write_text('window.CLOUDHUB = '+json.dumps(cfg,ensure_ascii=False)+';\n',encoding='utf-8')
print('Written public config '+str(dest))
