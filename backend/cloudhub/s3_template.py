"""T02 environment resources only; shared backup bucket stays in the control plane."""
from .s3_files import bucket_name

def s3_template(env, settings):
    tags = [{"Key": "cloudhub:environment", "Value": env["environment_id"]},
            {"Key": "cloudhub:owner", "Value": env["owner_id"]}, {"Key": "cloudhub:managed", "Value": "true"}]
    resources = {
        "DataBucket": {"Type": "AWS::S3::Bucket", "Properties": {"BucketName": bucket_name(env, settings),
            "VersioningConfiguration": {"Status": "Enabled"}, "Tags": tags,
            "PublicAccessBlockConfiguration": {k: True for k in ["BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets"]},
            "BucketEncryption": {"ServerSideEncryptionConfiguration": [{"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]},
            "OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}}},
        "OriginAccess": {"Type": "AWS::CloudFront::OriginAccessControl", "Properties": {"OriginAccessControlConfig": {
            "Name": "cloudhub-" + env["environment_id"], "OriginAccessControlOriginType": "s3", "SigningBehavior": "always", "SigningProtocol": "sigv4"}}},
        "Distribution": {"Type": "AWS::CloudFront::Distribution", "Properties": {"Tags": tags, "DistributionConfig": {
            "Enabled": True, "DefaultRootObject": "index.html", "HttpVersion": "http2", "PriceClass": "PriceClass_100",
            "Origins": [{"Id": "private-s3", "DomainName": {"Fn::GetAtt": ["DataBucket", "RegionalDomainName"]},
                "S3OriginConfig": {"OriginAccessIdentity": ""}, "OriginAccessControlId": {"Ref": "OriginAccess"}}],
            "DefaultCacheBehavior": {"TargetOriginId": "private-s3", "ViewerProtocolPolicy": "redirect-to-https",
                "AllowedMethods": ["GET", "HEAD"], "CachedMethods": ["GET", "HEAD"], "Compress": True,
                "CachePolicyId": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad", "ResponseHeadersPolicyId": "67f7725c-6f97-4210-82d7-5512b31e9d03"},
            "ViewerCertificate": {"CloudFrontDefaultCertificate": True}}}},
        "BucketPolicy": {"Type": "AWS::S3::BucketPolicy", "Properties": {"Bucket": {"Ref": "DataBucket"}, "PolicyDocument": {
            "Version": "2012-10-17", "Statement": [
                {"Effect": "Allow", "Principal": {"Service": "cloudfront.amazonaws.com"}, "Action": "s3:GetObject",
                    "Resource": {"Fn::Sub": "${DataBucket.Arn}/*"}, "Condition": {"StringEquals": {"AWS:SourceArn": {"Fn::Sub": "arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:distribution/${Distribution}"}}}},
                {"Effect": "Deny", "Principal": "*", "Action": "s3:*", "Resource": [{"Fn::GetAtt": ["DataBucket", "Arn"]}, {"Fn::Sub": "${DataBucket.Arn}/*"}],
                    "Condition": {"Bool": {"aws:SecureTransport": "false"}}}]}}}}
    if env['template_id'] != 'T02':
        resources.pop('Distribution');resources.pop('OriginAccess')
        resources['BucketPolicy']['Properties']['PolicyDocument']['Statement'].pop(0)
        return {'AWSTemplateFormatVersion':'2010-09-09','Resources':resources,'Outputs':{'BucketName':{'Value':{'Ref':'DataBucket'}}}}
    return {"AWSTemplateFormatVersion": "2010-09-09", "Resources": resources, "Outputs": {
        "BucketName": {"Value": {"Ref": "DataBucket"}}, "DistributionId": {"Value": {"Ref": "Distribution"}},
        "WebsiteUrl": {"Value": {"Fn::Sub": "https://${Distribution.DomainName}"}}}}
