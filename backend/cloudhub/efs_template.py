"""T08: two managed Linux nodes, encrypted EFS and TLS mounts; no public NFS/SSH."""
import base64
from pathlib import Path
from .cf_common import att,instance,name,ref,sg,tags,template
def efs_template(env,settings,recovery=None):
    r={'NodeGroup':sg(env,settings,'CloudHub managed EFS nodes'),
       'FileGroup':sg(env,settings,'Only environment nodes reach NFS',[{'IpProtocol':'tcp','FromPort':2049,'ToPort':2049,'SourceSecurityGroupId':ref('NodeGroup')}]),
       'FileSystem':{'Type':'AWS::EFS::FileSystem','Properties':{'Encrypted':True,'PerformanceMode':'generalPurpose','ThroughputMode':'bursting',
            'BackupPolicy':{'Status':'DISABLED'},'FileSystemTags':tags(env)}}}
    outputs={'FileSystemId':ref('FileSystem')}
    encoded=base64.b64encode(Path(__file__).with_name('efs_records.py').read_bytes()).decode()
    for i,subnet in enumerate(settings['public_subnet_ids'][:2],1):
        mount='Mount'+str(i);node='Node'+str(i)
        r[mount]={'Type':'AWS::EFS::MountTarget','Properties':{'FileSystemId':ref('FileSystem'),'SubnetId':subnet,'SecurityGroups':[ref('FileGroup')]}}
        script="""#!/bin/bash
set -euo pipefail
dnf install -y amazon-efs-utils python3
mkdir -p /shared /opt/cloudhub
echo '${Helper}' | base64 --decode > /opt/cloudhub/records.py
echo '${FileSystem}:/ /shared efs _netdev,tls 0 0' >> /etc/fstab
for attempt in $(seq 1 30); do mount /shared && break || sleep 10; done
mountpoint -q /shared
mkdir -p /shared/records
if [ ! -f /shared/records/demo ]; then printf 'CloudHub EFS demo' > /shared/records/demo; fi
"""
        node_settings={**settings,'public_subnet_id':subnet}
        r[node]=instance(env,node_settings,[ref('NodeGroup')],settings['instance_profile'],{'Fn::Sub':[script,{'Helper':encoded}]})
        r[node]['DependsOn']=['Mount1','Mount2'];outputs[node+'Id']=ref(node)
    return template(r,outputs)
