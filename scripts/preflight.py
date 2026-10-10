"""Read-only AWS preflight. Does not create resources, upgrade plans or issue keys."""
import argparse,json,re
from pathlib import Path
from datetime import datetime,timezone

def inspect(config,session):
    report={'mode':'AWS_READ_ONLY','recorded_at':datetime.now(timezone.utc).isoformat(),'checks':{},'passed':False}
    def check(name,fn):
        try:report['checks'][name]={'passed':True,'observations':fn()}
        except Exception as exc:report['checks'][name]={'passed':False,'error':type(exc).__name__+': '+str(exc)}
    identity=session.client('sts').get_caller_identity()
    if identity['Account']!=config['account_id']:raise ValueError('AWS account mismatch; stop')
    report.update(account_id=identity['Account'],region=config['region'],principal_arn=identity['Arn'])
    def plan():
        state=session.client('freetier',region_name='us-east-1').get_account_plan_state()
        state.pop('ResponseMetadata',None)
        if config.get('cash_budget_usd',0)==0 and state.get('accountPlanType')!='FREE':raise ValueError('Zero cash requires confirmed FREE plan; no automatic upgrade allowed')
        credit=state.get('accountPlanRemainingCredits',{})
        if credit.get('unit')!='USD' or float(credit.get('amount',0))<config.get('budget_usd',0):raise ValueError('Available USD credits do not cover the gross budget')
        return state
    check('account_plan',plan)
    ec2=session.client('ec2');rds=session.client('rds')
    def network():
        keys=['public_subnet_id','public_subnet_2_id','db_subnet_1_id','db_subnet_2_id'];ids=[config[k] for k in keys]
        subnets=ec2.describe_subnets(SubnetIds=ids)['Subnets'];byid={s['SubnetId']:s for s in subnets}
        if len(byid)!=4 or any(s['VpcId']!=config['vpc_id'] for s in subnets):raise ValueError('Need 4 distinct approved subnets in one VPC')
        for pair in [ids[:2],ids[2:]]:
            if len({byid[v]['AvailabilityZone'] for v in pair})!=2:raise ValueError('Public and DB pairs each require 2 AZs')
        if byid[ids[0]]['AvailabilityZone']!=config['availability_zone']:raise ValueError('T01 data volume/instance AZ mismatch')
        routes=ec2.describe_route_tables(Filters=[{'Name':'vpc-id','Values':[config['vpc_id']]}])['RouteTables']
        for sid in ids[:2]:
            route=next((r for r in routes if any(a.get('SubnetId')==sid for a in r['Associations'])),None)
            if route is None:route=next(r for r in routes if any(a.get('Main') for a in r['Associations']))
            if not any(r.get('DestinationCidrBlock')=='0.0.0.0/0' and r.get('GatewayId','').startswith('igw-') and r.get('State')=='active' for r in route['Routes']):raise ValueError('Public subnet lacks active IGW route')
        for attr in ['enableDnsSupport','enableDnsHostnames']:
            if not ec2.describe_vpc_attribute(VpcId=config['vpc_id'],Attribute=attr)[attr[0].upper()+attr[1:]]['Value']:raise ValueError('VPC DNS missing')
        return [{'subnet_id':s['SubnetId'],'az':s['AvailabilityZone'],'vpc_id':s['VpcId']} for s in subnets]
    check('approved_network',network)
    def image():
        value=ec2.describe_images(ImageIds=[config['ami_id']],Owners=['amazon'])['Images']
        if len(value)!=1 or value[0]['Architecture']!='x86_64' or value[0]['State']!='available' or not value[0]['Name'].startswith('al2023-ami-'):raise ValueError('AMI must be current approved Amazon Linux 2023 x86_64')
        return {'id':value[0]['ImageId'],'name':value[0]['Name']}
    check('linux_ami',image)
    def database():
        rows=[]
        for page in rds.get_paginator('describe_orderable_db_instance_options').paginate(Engine='postgres',EngineVersion=config['postgres_version'],DBInstanceClass='db.t3.micro',Vpc=True):rows.extend(page['OrderableDBInstanceOptions'])
        if not any(v.get('StorageType')=='gp3' and v.get('SupportsStorageEncryption') for v in rows):raise ValueError('Approved PostgreSQL version/class/gp3 unavailable in region')
        return {'version':config['postgres_version'],'db_class':'db.t3.micro','orderable':True}
    if {'T03','T04'}&set(config.get('validation_templates',[])):check('postgres_version',database)
    def container():
        match=re.fullmatch(re.escape(config['account_id'])+r'\.dkr\.ecr\.'+re.escape(config['region'])+r'\.amazonaws\.com/(.+)@sha256:([a-f0-9]{64})',config['approved_image'])
        if not match:raise ValueError('Reviewed own-account image digest missing')
        detail=session.client('ecr').describe_images(repositoryName=match[1],imageIds=[{'imageDigest':'sha256:'+match[2]}])['imageDetails']
        if not detail:raise ValueError('Image digest not present')
        return {'repository':match[1],'digest':detail[0]['imageDigest'],'image_size_bytes':detail[0].get('imageSizeInBytes'),'contract':'Run /health and S3 /files fixture in AWS lifecycle harness before verification'}
    if 'T06' in config.get('validation_templates',[]):check('approved_container_image',container)
    def redshift():
        client=session.client('redshift-serverless');client.list_workgroups(maxResults=1)
        return {'read_api_accessible':True,'create_and_restore_still_require_live_validation':True}
    if 'T10' in config.get('validation_templates',[]):check('redshift_plan_access',redshift)
    def quotas():
        observations={}
        for service in ['ec2','fargate','lambda','redshift']:
            client=session.client('service-quotas');rows=[]
            for page in client.get_paginator('list_service_quotas').paginate(ServiceCode=service):
                rows.extend({'name':v['QuotaName'],'value':v['Value'],'code':v['QuotaCode']} for v in page['Quotas'])
            observations[service]=rows
        return observations
    check('service_quotas_for_operator_review',quotas)
    # A successful read API call does not prove permission to create every service.
    report['permissions_to_create']='NOT_VERIFIED; review rendered CF/IAM policies and deployment changeset'
    report['passed']=all(v['passed'] for v in report['checks'].values())
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--out',required=True);args=p.parse_args()
    config=json.loads(Path(args.config).read_text(encoding='utf-8'));import boto3
    session=boto3.Session(profile_name=config.get('profile') or None,region_name=config['region'])
    report=inspect(config,session);out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,default=str,indent=2),encoding='utf-8')
    print('Preflight passed:',report['passed']);return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
