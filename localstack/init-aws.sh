#!/bin/bash
set -e

REGION="${AWS_DEFAULT_REGION:-us-east-1}"
ENDPOINT="http://localhost:4566"
LOG_GROUP="${LOG_GROUP:-/microservice/payment-service}"
LOG_STREAM="${LOG_STREAM:-application}"
TOPIC_NAME="anomaly-findings"

echo "==> Creating CloudWatch log group: $LOG_GROUP"
aws --endpoint-url=$ENDPOINT --region=$REGION logs create-log-group \
    --log-group-name "$LOG_GROUP" 2>/dev/null || echo "    (already exists)"

echo "==> Creating CloudWatch log stream: $LOG_STREAM"
aws --endpoint-url=$ENDPOINT --region=$REGION logs create-log-stream \
    --log-group-name "$LOG_GROUP" \
    --log-stream-name "$LOG_STREAM" 2>/dev/null || echo "    (already exists)"

echo "==> Creating SNS topic: $TOPIC_NAME"
TOPIC_ARN=$(aws --endpoint-url=$ENDPOINT --region=$REGION sns create-topic \
    --name "$TOPIC_NAME" \
    --query 'TopicArn' --output text)

echo "==> SNS Topic ARN: $TOPIC_ARN"
echo "SNS_TOPIC_ARN=$TOPIC_ARN" >> /etc/localstack/init/sns_topic_arn.env

echo "==> LocalStack init complete."
