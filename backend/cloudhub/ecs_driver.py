"""T06 reuses S3 data backup; container writable/ephemeral filesystem is excluded."""
import urllib.request,urllib.error
from .stack_driver import StackDriver
from .s3_driver import S3Driver
from .ecs_template import ecs_template
class ECSDriver(StackDriver):
    def __init__(self,settings,clients=None):
        super().__init__(settings,clients);self.files=None
    def file_driver(self):
        if self.files is None:self.files=S3Driver(self.settings)
        return self.files
    def build_template(self,env,recovery):return ecs_template(env,self.settings,recovery)
    def apply_outputs(self,env):
        env.update(bucket_name=env['outputs']['BucketName'],access_address=env['outputs']['WebsiteUrl'],data_source=env['outputs']['BucketName'])
    def inventory(self,env):
        super().inventory(env)
        self.file_driver().inventory(env)
        cluster=next((r['id'] for r in env['resources'] if r['type']=='AWS::ECS::Cluster'),None)
        if cluster:
            for page in self.ecs.get_paginator('list_tasks').paginate(cluster=cluster):
                for task in self.ecs.describe_tasks(cluster=cluster,tasks=page['taskArns']).get('tasks',[]) if page['taskArns'] else []:
                    items=[{'type':'ECS::Task','id':task['taskArn'],'cluster':cluster}]
                    for attachment in task.get('attachments',[]):
                        items += [{'type':'AWS::EC2::NetworkInterface','id':v['value']} for v in attachment.get('details',[]) if v['name']=='networkInterfaceId']
                    for item in items:
                        if not any(v['type']==item['type'] and v['id']==item['id'] for v in env['resources']):env['resources'].append(item)
        return env['resources']
    def ready(self,env,op,recovery):
        if not self.file_driver().ready(env,op,recovery):return False
        output=env['outputs'];tasks=self.ecs.describe_services(cluster=output['Cluster'],services=[output['Service']])['services']
        if not tasks or tasks[0]['runningCount']!=1 or tasks[0]['pendingCount']:return False
        targets=self.elbv2.describe_target_health(TargetGroupArn=output['TargetGroupArn'])['TargetHealthDescriptions']
        if not targets or not all(t['TargetHealth']['State']=='healthy' for t in targets):return False
        try:
            with urllib.request.urlopen(env['access_address']+'/health',timeout=5) as response:return response.status==200
        except (urllib.error.URLError,TimeoutError):return False
    def backup(self,env,op,rp):return self.file_driver().backup(env,op,rp)
    def write_file(self,env,op):return self.file_driver().write_file(env,op)
    def delete(self,env,op):
        self.inventory(env)
        if not self.file_driver().delete(env,op):return False
        return StackDriver.delete(self,env,op)
    def _exists(self,item):
        if item['type']=='ECS::Task':return any(t['lastStatus']!='STOPPED' for t in self.ecs.describe_tasks(cluster=item['cluster'],tasks=[item['id']])['tasks'])
        if item['type'] in {'AWS::S3::Bucket','AWS::S3::BucketPolicy'}:
            # S3 processor performs expected-owner/tag reconciliation separately.
            return None
        return super()._exists(item)
    def audit_backups(self,env,backups,report):
        scoped=self.file_driver().audit(env,backups)
        ids={r['id'] for r in env['resources'] if r['type'] in {'AWS::S3::Bucket','AWS::S3::BucketPolicy'}}
        for key in ['unknown','deleted','residual']:report[key]=[v for v in report[key] if v['id'] not in ids]
        for key in ['unknown','deleted','residual','retained']:
            report[key].extend(v for v in scoped[key] if v['type'].startswith(('AWS::S3','S3::')))
    def expire_backup(self,rp):return self.file_driver().expire_backup(rp)
