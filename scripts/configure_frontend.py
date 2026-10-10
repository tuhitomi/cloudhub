"""Read successful stack outputs; write public Cognito/API config only."""
import json
import argparse
from pathlib import Path
from build_pages_live import public_config


def config_from_outputs(data):
    if 'Stacks' in data:
        stack = data['Stacks'][0]
        if stack.get('StackStatus') not in {'CREATE_COMPLETE', 'UPDATE_COMPLETE'}:
            raise ValueError('Only successful deployed stack outputs can configure the website')
        outputs = stack['Outputs']
    else:
        outputs = data
    values = {entry['OutputKey']: entry['OutputValue'] for entry in outputs}
    return public_config({'configured': True, 'apiBase': values['ApiUrl'], 'clientId': values['ClientId'],
                          'cognitoDomain': values['CognitoDomain'], 'redirectUri': values['FrontendUrl'],
                          'region': values['Region']})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stack_outputs', type=Path)
    parser.add_argument('--out-json', type=Path, help='Write only public JSON for the live Pages builder')
    args = parser.parse_args()
    config = config_from_outputs(json.loads(args.stack_outputs.read_text(encoding='utf-8')))
    if args.out_json:
        target = args.out_json
        content = json.dumps(config, ensure_ascii=False, indent=2) + '\n'
    else:
        target = Path(__file__).resolve().parents[1] / 'frontend/config.js'
        content = 'window.CLOUDHUB = ' + json.dumps(config, ensure_ascii=False) + ';\n'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
    print('Written public config ' + str(target))


if __name__ == '__main__':
    main()
