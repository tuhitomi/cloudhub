"""Owned SSH key retrieval, no AWS access keys or embedded web terminal."""
from .errors import HubError

def ssh_key(service, owner, eid, clients=None):
    env = service.owned("E", eid, owner)
    if env["status"] != "READY" or env.get("lock") or env["deadline"] <= int(service.clock()):
        raise HubError("ACCESS_UNAVAILABLE", "Môi trường chưa sẵn sàng hoặc đã hết hạn.", 409)
    if not env.get("key_pair_id") or not env.get("public_ip"):
        raise HubError("ACCESS_UNAVAILABLE", "Chưa có khóa SSH hoặc địa chỉ truy cập.", 409)
    import boto3
    clients = clients or {}
    ec2 = clients.get("ec2") or boto3.client("ec2")
    ssm = clients.get("ssm") or boto3.client("ssm")
    pair = ec2.describe_key_pairs(KeyPairIds=[env["key_pair_id"]])["KeyPairs"][0]
    tags = {t["Key"]: t["Value"] for t in pair.get("Tags", [])}
    if tags.get("cloudhub:environment") != eid or tags.get("cloudhub:owner") != owner:
        raise HubError("RESOURCE_SCOPE_MISMATCH", "Khóa SSH không thuộc môi trường.", 409)
    value = ssm.get_parameter(Name="/ec2/keypair/" + env["key_pair_id"], WithDecryption=True)["Parameter"]["Value"]
    return {"environment_id": eid, "filename": eid + ".pem", "private_key": value,
            "public_ip": env["public_ip"], "username": "ec2-user", "port": 22,
            "command": "ssh -i " + eid + ".pem ec2-user@" + env["public_ip"]}
