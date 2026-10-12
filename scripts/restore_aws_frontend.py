"""Add the original private S3/CloudFront frontend to an existing SAM backend.

The input must be the current stack's Original template. Existing resource IDs,
parameters, code artifacts, data references and verification gates are preserved.
This command only prepares a template; review a CloudFormation changeset before
executing it.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from generate_infra import build_template, sub

FRONTEND = ('FrontendBucket', 'OriginAccess', 'FrontendDistribution', 'FrontendBucketPolicy')


def restore_frontend(current):
    template = deepcopy(current)
    resources = template['Resources']
    if any(name in resources for name in FRONTEND):
        raise ValueError('Frontend already present or partially deployed; inspect the stack first')
    native = build_template()
    for name in FRONTEND:
        resources[name] = deepcopy(native['Resources'][name])
    callback = sub('https://${FrontendDistribution.DomainName}/')
    origin = sub('https://${FrontendDistribution.DomainName}')
    resources['UserClient']['Properties'].update(CallbackURLs=[callback], LogoutURLs=[callback])
    resources['HttpApi']['Properties']['CorsConfiguration']['AllowOrigins'] = [origin]
    for resource in resources.values():
        if resource['Type'] != 'AWS::Serverless::Function':
            continue
        settings = resource['Properties'].get('Environment', {}).get('Variables', {}).get('DRIVER_SETTINGS')
        if settings is None:
            continue
        if not isinstance(settings, dict) or set(settings) != {'Fn::Sub'} or not isinstance(settings['Fn::Sub'], str):
            raise ValueError('Unexpected driver settings; refusing an unsafe migration')
        parsed = json.loads(settings['Fn::Sub'])
        parsed['frontend_origin'] = 'https://${FrontendDistribution.DomainName}'
        settings['Fn::Sub'] = json.dumps(parsed, separators=(',', ':'))
    for key in ('FrontendUrl', 'FrontendBucket', 'DistributionId'):
        template['Outputs'][key] = deepcopy(native['Outputs'][key])
    return template


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('current_template', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = restore_frontend(json.loads(args.current_template.read_text(encoding='utf-8')))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Prepared AWS frontend migration: ' + str(args.out))


if __name__ == '__main__':
    main()
