#!/bin/bash
set -e

REGION="${AWS_DEFAULT_REGION:-us-east-1}"
ENDPOINT="http://localhost:4566"
LOG_GROUP="${LOG_GROUP:-/microservice/payment-service}"
LOG_STREAM="${LOG_STREAM:-application}"
TOPIC_NAME="anomaly-findings"
QUEUE_NAME="anomaly-findings-watcher"

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
echo "    SNS Topic ARN: $TOPIC_ARN"

echo "==> Creating SQS queue: $QUEUE_NAME"
QUEUE_URL=$(aws --endpoint-url=$ENDPOINT --region=$REGION sqs create-queue \
    --queue-name "$QUEUE_NAME" \
    --query 'QueueUrl' --output text)
echo "    SQS Queue URL: $QUEUE_URL"

QUEUE_ARN=$(aws --endpoint-url=$ENDPOINT --region=$REGION sqs get-queue-attributes \
    --queue-url "$QUEUE_URL" \
    --attribute-names QueueArn \
    --query 'Attributes.QueueArn' --output text)
echo "    SQS Queue ARN: $QUEUE_ARN"

echo "==> Subscribing SQS queue to SNS topic"
aws --endpoint-url=$ENDPOINT --region=$REGION sns subscribe \
    --topic-arn "$TOPIC_ARN" \
    --protocol sqs \
    --notification-endpoint "$QUEUE_ARN" > /dev/null

echo "==> Writing env file for containers"
cat > /tmp/localstack_outputs.env <<EOF
SNS_TOPIC_ARN=$TOPIC_ARN
SQS_QUEUE_URL=$QUEUE_URL
EOF

echo "==> LocalStack init complete."
echo "    SNS_TOPIC_ARN=$TOPIC_ARN"
echo "    SQS_QUEUE_URL=$QUEUE_URL"
