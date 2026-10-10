"""No AWS calls: all-template resource/cost/cleanup review, including incomplete drafts."""
import argparse,ipaddress,json,re,sys
from pathlib import Path
from decimal import Decimal,InvalidOperation
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from cloudhub.catalog import IMPLEMENTED,NAMES,SCOPES,configuration
from cloudhub.cost import Estimator,RATE_REQUIREMENTS

def prepare(config,prices):
    errors=[];region=config.get('region','')
    patterns={'account_id':r'\d{12}','region':r'[a-z]{2}-[a-z]+-\d','vpc_id':r'vpc-[a-f0-9]{8,17}',
        'public_subnet_id':r'subnet-[a-f0-9]{8,17}','public_subnet_2_id':r'subnet-[a-f0-9]{8,17}',
        'db_subnet_1_id':r'subnet-[a-f0-9]{8,17}','db_subnet_2_id':r'subnet-[a-f0-9]{8,17}','ami_id':r'ami-[a-f0-9]{8,17}','postgres_version':r'\d+\.\d+'}
    for key,pattern in patterns.items():
        if not re.fullmatch(pattern,str(config.get(key,''))):errors.append(key+' missing/invalid')
    if not re.fullmatch(re.escape(region)+r'[a-z]',config.get('availability_zone','')):errors.append('AZ missing/invalid')
    for key in ['acknowledged','preflight_passed']:
        if config.get(key) is not True:errors.append(key+' not confirmed')
    try:
        cidr=ipaddress.ip_network(config.get('ssh_cidr',''),strict=True)
        if cidr.version!=4 or cidr.prefixlen<24:errors.append('SSH requires IPv4 /24 or narrower')
    except ValueError:errors.append('Approved SSH CIDR missing')
    try:
        budget=Decimal(str(config.get('budget_usd',0)))
        if not budget.is_finite() or budget<=0:errors.append('Positive finite USD budget required');budget=Decimal(0)
    except InvalidOperation:errors.append('Invalid budget');budget=Decimal(0)
    selected=config.get('validation_templates',[])
    if not selected or set(selected)-IMPLEMENTED:errors.append('Valid validation_templates required')
    if config.get('verified_templates'):errors.append('First deployment requires verified_templates=[]')
    if 'T06' in selected and not re.fullmatch(re.escape(config.get('account_id',''))+r'\.dkr\.ecr\.'+re.escape(region)+r'\.amazonaws\.com/[a-z0-9/_-]+@sha256:[a-f0-9]{64}',config.get('approved_image','')):errors.append('Reviewed T06 ECR digest missing')
    if prices.get('approved') is not True:errors.append('Price review not yet approved')
    if prices.get('region')!=region or not prices.get('source_urls') or not prices.get('updated_at'):errors.append('Prices need region/date/source URLs')
    required=set().union(*(RATE_REQUIREMENTS[t] for t in selected if t in IMPLEMENTED))
    if required-set(prices.get('units',{})):errors.append('Missing rates: '+','.join(sorted(required-set(prices.get('units',{})))))
    valid_rates=True
    for key,value in prices.get('units',{}).items():
        try:
            number=Decimal(str(value))
            if not number.is_finite() or number<0:errors.append('Invalid rate '+key);valid_rates=False
        except InvalidOperation:errors.append('Invalid rate '+key);valid_rates=False
    if config.get('retention_days',7)!=7:errors.append('Only retention_days=7 implemented')
    planning={**prices,'approved':True}
    quotes={t:Estimator(planning).estimate(t,1) for t in selected if valid_rates and t in IMPLEMENTED and RATE_REQUIREMENTS[t]<=prices.get('units',{}).keys()}
    total=sum(q['total_milliusd'] for q in quotes.values())*6
    if len(quotes)!=len(selected):errors.append('Not all estimates available')
    if budget*1000<total+10000:errors.append('Budget smaller than two-cycle estimate plus shared allowance')
    mapping={'VpcId':'vpc_id','PublicSubnetId':'public_subnet_id','PublicSubnet2Id':'public_subnet_2_id','DbSubnet1Id':'db_subnet_1_id','DbSubnet2Id':'db_subnet_2_id',
        'AvailabilityZone':'availability_zone','AmiId':'ami_id','SshCidr':'ssh_cidr','PostgresVersion':'postgres_version','ApprovedImage':'approved_image'}
    compact={k:prices[k] for k in ['approved','region','updated_at','units'] if k in prices}
    parameters={p:config.get(k,'') for p,k in mapping.items()}
    parameters.update(BudgetMilliusd=str(int(budget*1000)),PricesJson=json.dumps(compact,separators=(',',':')),VerifiedTemplates='',ValidationTemplates=','.join(selected),
        UserLimit=str(config.get('max_user_environments',2)),TotalLimit=str(config.get('max_total_environments',4)))
    receiver=config.get('alert_email','')
    if receiver and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',receiver):errors.append('Invalid alert_email')
    parameters.update(AlertEmail=receiver,ActualCostsEnabled='true' if config.get('actual_costs_enabled') is True else 'false')
    return {'not_deployed':True,'deployable':not errors,'blockers':errors,'deployment_config':config,'planning_only_estimates_1h':quotes,
        'conservative_two_cycles_per_template_milliusd':total,'shared_allowance_usd':10,
        'cost_method':'2 cycles x (source + restored + extra full quote for backup/cleanup reserve) = 6 quotes/template; 20% contingency included. No credits/free tiers deducted.',
        'budget_proposal_usd':str(budget),'cash_budget_usd':config.get('cash_budget_usd',0),
        'templates':[{'template_id':t,'name':n,'configuration':configuration(t),'services':svc,'backup_scope':SCOPES[t]} for t,n,_,svc in NAMES],
        'control_plane_resources':{k:v['Type'] for k,v in json.loads((ROOT/'infra/control-plane.json').read_text())['Resources'].items()},
        'parameters_for_review':[{'ParameterKey':k,'ParameterValue':v} for k,v in parameters.items()],
        'cleanup':'Backup then destroy owned environments; reconcile exact resource IDs including ENI/EBS/secrets/task definitions/snapshots. Shared table, buckets, vault retained. Network, image repository and packaging resources require separate cleanup review.',
        'rights_review':'Review IAM creation/PassRole, boundaries, service-linked roles, AWS Backup managed roles and regional create/describe wildcards before actual action; no root access keys.'}

def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--prices',required=True);p.add_argument('--draft',action='store_true');args=p.parse_args()
    doc=prepare(json.loads(Path(args.config).read_text(encoding='utf-8')),json.loads(Path(args.prices).read_text(encoding='utf-8')))
    (ROOT/'config/deployment-review.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding='utf-8')
    target=ROOT/'config/sam-parameters.json'
    if doc['deployable']:target.write_text(json.dumps(doc['parameters_for_review'],indent=2),encoding='utf-8')
    elif target.exists():target.unlink()
    print('Review saved; deployable:',doc['deployable'],'; blockers:',len(doc['blockers']))
    return 0 if doc['deployable'] or args.draft else 1
if __name__=='__main__':raise SystemExit(main())
