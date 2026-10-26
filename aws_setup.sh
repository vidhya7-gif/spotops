#!/bin/bash
# SpotOps AWS environment setup. Paste/run in AWS CloudShell (region ap-southeast-2, Sydney).
#   bash aws_setup.sh                       -> S3 + IAM + CloudWatch
#   EMAIL=you@x.com bash aws_setup.sh       -> also e-mail alerts through SNS
#   LAUNCH_SPOT=1 bash aws_setup.sh         -> also launch a REAL EC2 Spot instance
# Safe to re-run: "already exists" messages are fine.
export AWS_DEFAULT_REGION=ap-southeast-2
REGION=$AWS_DEFAULT_REGION
ACCT=$(aws sts get-caller-identity --query Account --output text) || { echo "No AWS credentials"; exit 1; }
BUCKET=spotops-ckpt-$ACCT
ROLE=spotops-ec2-role
PROFILE=spotops-ec2-profile
INSTANCES="i-07a0094547dc0df68 i-09e632e2f7bc0c623 i-0077951e936d4d733"   # spot, fallback, storage
SG=sg-0ee7c07de8b248930
AMI=$(aws ec2 describe-instances --instance-ids i-07a0094547dc0df68 --query 'Reservations[0].Instances[0].ImageId' --output text)   # same image as your existing VMs

echo "== 1/5 S3 bucket: $BUCKET (private, versioned)"
aws s3api create-bucket --bucket $BUCKET --create-bucket-configuration LocationConstraint=$REGION >/dev/null 2>&1 || echo "   (exists)"
aws s3api put-public-access-block --bucket $BUCKET --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-versioning --bucket $BUCKET --versioning-configuration Status=Enabled

echo "== 2/5 IAM role with least privilege (S3 checkpoints/ + CloudWatch namespace SpotOps only)"
cat > trust.json <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}
JSON
cat > policy.json <<JSON
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":["s3:PutObject","s3:GetObject","s3:DeleteObject"],"Resource":"arn:aws:s3:::$BUCKET/checkpoints/*"},
 {"Effect":"Allow","Action":"s3:ListBucket","Resource":"arn:aws:s3:::$BUCKET"},
 {"Effect":"Allow","Action":"cloudwatch:PutMetricData","Resource":"*","Condition":{"StringEquals":{"cloudwatch:namespace":"SpotOps"}}}]}
JSON
aws iam create-role --role-name $ROLE --assume-role-policy-document file://trust.json >/dev/null 2>&1 || echo "   (role exists)"
aws iam put-role-policy --role-name $ROLE --policy-name spotops-least-privilege --policy-document file://policy.json
aws iam create-instance-profile --instance-profile-name $PROFILE >/dev/null 2>&1 || echo "   (profile exists)"
aws iam add-role-to-instance-profile --instance-profile-name $PROFILE --role-name $ROLE >/dev/null 2>&1
sleep 10
for i in $INSTANCES; do
  aws ec2 associate-iam-instance-profile --instance-id $i --iam-instance-profile Name=$PROFILE >/dev/null 2>&1 \
    && echo "   role attached to $i" || echo "   $i already has a profile (ok)"
done

echo "== 3/5 SNS e-mail alerts (optional)"
ACTION=""
if [ -n "${EMAIL:-}" ]; then
  TOPIC=$(aws sns create-topic --name spotops-alerts --query TopicArn --output text)
  aws sns subscribe --topic-arn $TOPIC --protocol email --notification-endpoint "$EMAIL" >/dev/null
  ACTION="--alarm-actions $TOPIC"
  echo "   confirm the subscription e-mail AWS just sent to $EMAIL"
else echo "   skipped (run with EMAIL=you@example.com to enable)"; fi

echo "== 4/5 CloudWatch alarms + dashboard"
aws cloudwatch put-metric-alarm --alarm-name spotops-training-stalled $ACTION \
  --alarm-description "No training heartbeat for 3 minutes (also ALARM when no run is active)" \
  --namespace SpotOps --metric-name Epoch --statistic Maximum --period 60 --evaluation-periods 3 \
  --threshold 1 --comparison-operator LessThanThreshold --treat-missing-data breaching
aws cloudwatch put-metric-alarm --alarm-name spotops-spot-interruption-notice $ACTION \
  --alarm-description "AWS issued the 2-minute Spot interruption notice" \
  --namespace SpotOps --metric-name SpotInterruptionNotice --statistic Sum --period 60 --evaluation-periods 1 \
  --threshold 1 --comparison-operator GreaterThanOrEqualToThreshold --treat-missing-data notBreaching
cat > dash.json <<JSON
{"widgets":[
 {"type":"metric","x":0,"y":0,"width":12,"height":6,"properties":{"title":"Training accuracy (%)","region":"$REGION","metrics":[["SpotOps","Accuracy"]],"stat":"Maximum","period":60,"view":"timeSeries"}},
 {"type":"metric","x":12,"y":0,"width":12,"height":6,"properties":{"title":"Epoch (heartbeat)","region":"$REGION","metrics":[["SpotOps","Epoch"]],"stat":"Maximum","period":60,"view":"timeSeries"}},
 {"type":"metric","x":0,"y":6,"width":12,"height":6,"properties":{"title":"Spot interruption notices","region":"$REGION","metrics":[["SpotOps","SpotInterruptionNotice"]],"stat":"Sum","period":60,"view":"timeSeries"}}]}
JSON
aws cloudwatch put-dashboard --dashboard-name SpotOps --dashboard-body file://dash.json >/dev/null

echo "== 5/5 Real Spot instance (optional)"
if [ "${LAUNCH_SPOT:-0}" = "1" ]; then
  ID=$(aws ec2 run-instances --image-id $AMI --instance-type t3.micro --key-name spotops-aws-key-v2 \
    --security-group-ids $SG --iam-instance-profile Name=$PROFILE \
    --instance-market-options 'MarketType=spot,SpotOptions={SpotInstanceType=one-time,InstanceInterruptionBehavior=terminate}' \
    --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=spotops-real-spot-vm}]' \
    --query 'Instances[0].InstanceId' --output text)
  echo "   launched $ID, waiting..."; aws ec2 wait instance-running --instance-ids $ID
  echo "   REAL SPOT VM PUBLIC IP: $(aws ec2 describe-instances --instance-ids $ID --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)"
else echo "   skipped (run with LAUNCH_SPOT=1 when you are ready)"; fi

echo; echo "DONE. Bucket: $BUCKET   Role: $ROLE   Dashboard: CloudWatch > Dashboards > SpotOps"
