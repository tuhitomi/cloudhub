"""D09/D10: shared Linux + EBS builder, reused verbatim by T05."""
import base64
import json

def linux_template(env, settings, snapshot_id=None):
    eid = env["environment_id"]
    tags = [{"Key": "cloudhub:environment", "Value": eid},
            {"Key": "cloudhub:owner", "Value": env["owner_id"]},
            {"Key": "cloudhub:managed", "Value": "true"}]
    volume = {"AvailabilityZone": {"Ref": "EnvironmentAvailabilityZone"}, "Size": 8, "VolumeType": "gp3",
              "Encrypted": True, "Tags": tags}
    if snapshot_id:
        volume["SnapshotId"] = snapshot_id
    # VolumeAttachment avoids an IAM role per environment. Instance only assumes shared SSM role.
    # Never reformat a restored volume. Explicit disk serial prevents selecting the root device.
    script = """#!/bin/bash
set -euxo pipefail
mkdir -p /data
for attempt in $(seq 1 120); do
  disk=$(lsblk -dn -o NAME,SERIAL | awk -v id='${VolumeSerial}' '$2 == id {print "/dev/" $1}')
  if [ -n "$disk" ]; then break; fi
  sleep 5
done
test -n "$disk"
if ! blkid "$disk"; then
  ${FormatCommand}
fi
uuid=$(blkid -s UUID -o value "$disk")
echo "UUID=$uuid /data ext4 defaults,nofail 0 2" >> /etc/fstab
mount /data
mkdir -p /data/.cloudhub
${DemoCommand}
${WebCommand}
touch /data/.cloudhub/ready
"""
    substitutions = {"VolumeSerial": {"Fn::Join": ["", {"Fn::Split": ["-", {"Ref": "DataVolume"}]}]},
        "FormatCommand": "exit 1 # restore must have an existing filesystem" if snapshot_id else 'mkfs.ext4 "$disk"',
        "DemoCommand": "test -f /data/demo.txt" if snapshot_id else "printf 'CloudHub demo dữ liệu\\n' > /data/demo.txt",
        "WebCommand": "true"}
    if env["template_id"] == "T05":
        substitutions["WebCommand"] = """dnf install -y nginx
if [ ! -f /data/index.html ]; then printf '<html lang="vi"><h1>CloudHub T05</h1></html>' > /data/index.html; fi
cat > /etc/nginx/nginx.conf <<'NGINX'
user nginx;
worker_processes auto;
events { worker_connections 256; }
http {
  include /etc/nginx/mime.types;
  server { listen 80; root /data; index index.html; location / { try_files $uri $uri/ =404; } }
}
NGINX
chmod 755 /data
systemctl enable --now nginx
curl --fail http://127.0.0.1/
"""
    ingress = [{"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "CidrIp": settings["ssh_cidr"]}]
    if env["template_id"] == "T05":
        ingress.append({"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80, "CidrIp": "0.0.0.0/0"})
    resources = {
        "KeyPair": {"Type": "AWS::EC2::KeyPair", "Properties": {"KeyName": "cloudhub-" + eid,
                    "KeyType": "ed25519", "KeyFormat": "pem", "Tags": tags}},
        "SecurityGroup": {"Type": "AWS::EC2::SecurityGroup", "Properties": {
            "VpcId": settings["vpc_id"], "GroupDescription": "CloudHub environment; SSH from approved CIDR",
            "SecurityGroupIngress": ingress, "SecurityGroupEgress": [{"IpProtocol": "-1", "CidrIp": "0.0.0.0/0"}], "Tags": tags}},
        "DataVolume": {"Type": "AWS::EC2::Volume", "DeletionPolicy": "Delete", "UpdateReplacePolicy": "Delete", "Properties": volume},
        "Instance": {"Type": "AWS::EC2::Instance", "Properties": {
            "ImageId": settings["ami_id"], "InstanceType": "t3.micro", "IamInstanceProfile": settings["instance_profile"],
            "PropagateTagsToVolumeOnCreation": True,
            "KeyName": {"Ref": "KeyPair"},
            "CreditSpecification": {"CPUCredits": "standard"},
            "MetadataOptions": {"HttpTokens": "required", "HttpEndpoint": "enabled"},
            "NetworkInterfaces": [{"DeviceIndex": "0", "AssociatePublicIpAddress": True,
                                   "SubnetId": settings["public_subnet_id"], "GroupSet": [{"Ref": "SecurityGroup"}]}],
            "BlockDeviceMappings": [{"DeviceName": "/dev/xvda", "Ebs": {"VolumeSize": 16, "VolumeType": "gp3", "Encrypted": True, "DeleteOnTermination": True}}],
            "Tags": tags, "UserData": {"Fn::Base64": {"Fn::Sub": [script, substitutions]}}}},
        "Attachment": {"Type": "AWS::EC2::VolumeAttachment", "Properties": {
            "InstanceId": {"Ref": "Instance"}, "VolumeId": {"Ref": "DataVolume"}, "Device": "/dev/sdf"}}
    }
    return {"AWSTemplateFormatVersion": "2010-09-09", "Description": "CloudHub " + env["template_id"],
            "Parameters": {"EnvironmentAvailabilityZone": {"Type": "AWS::EC2::AvailabilityZone::Name",
                "Default": settings["availability_zone"], "AllowedValues": [settings["availability_zone"]]}},
            "Resources": resources, "Outputs": {"KeyPairId": {"Value": {"Fn::GetAtt": ["KeyPair", "KeyPairId"]}}, "InstanceId": {"Value": {"Ref": "Instance"}},
            "DataVolumeId": {"Value": {"Ref": "DataVolume"}}, "PublicIp": {"Value": {"Fn::GetAtt": ["Instance", "PublicIp"]}}}}
