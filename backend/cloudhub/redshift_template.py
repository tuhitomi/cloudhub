"""T10 private warehouse, fixed capacity, managed credentials and approved S3 source."""
from .s3_template import s3_template
from .cf_common import allow,att,name,ref,role,sg,sub,tags,template
def redshift_template(env,settings,recovery=None):
    r=s3_template(env,settings)['Resources']
    r['WarehouseGroup']=sg(env,settings,'No public warehouse ingress; use Redshift Data API')
    r['CopyRole']=role(env,settings,'redshift.amazonaws.com',[allow('s3:ListBucket',att('DataBucket')),allow('s3:GetObject',sub('${DataBucket.Arn}/*'))],'copy')
    r['Namespace']={'Type':'AWS::RedshiftServerless::Namespace','Properties':{'NamespaceName':name(env,'-ns'),'DbName':'cloudhub','AdminUsername':'cloudhub',
        'ManageAdminPassword':True,'IamRoles':[att('CopyRole')],'DefaultIamRoleArn':att('CopyRole'),'Tags':tags(env)}}
    props={'WorkgroupName':name(env,'-wg'),'NamespaceName':ref('Namespace'),'BaseCapacity':8,'MaxCapacity':8,'EnhancedVpcRouting':False,
        'PubliclyAccessible':False,'SubnetIds':settings['db_subnet_ids'],'SecurityGroupIds':[ref('WarehouseGroup')],
        'ConfigParameters':[{'ParameterKey':'require_ssl','ParameterValue':'true'},{'ParameterKey':'query_execution_time','ParameterValue':'120'}],'Tags':tags(env)}
    if recovery:props['SnapshotName']=recovery['redshift_snapshot_name']
    r['Workgroup']={'Type':'AWS::RedshiftServerless::Workgroup','Properties':props}
    return template(r,{'NamespaceName':ref('Namespace'),'WorkgroupName':ref('Workgroup'),'BucketName':ref('DataBucket'),'CopyRole':att('CopyRole'),
        'WorkgroupArn':att('Workgroup','Workgroup.WorkgroupArn')})
