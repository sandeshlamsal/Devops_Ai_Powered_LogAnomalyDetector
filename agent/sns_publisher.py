import json
import logging
import os

import boto3

log = logging.getLogger(__name__)

ENDPOINT = os.environ.get("LOCALSTACK_ENDPOINT", "http://localhost:4566")
REGION   = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
KEY_ID   = os.environ.get("AWS_ACCESS_KEY_ID", "test")
SECRET   = os.environ.get("AWS_SECRET_ACCESS_KEY", "test")


def _boto(service: str):
    return boto3.client(service, endpoint_url=ENDPOINT, region_name=REGION,
                        aws_access_key_id=KEY_ID, aws_secret_access_key=SECRET)


class SNSPublisher:
    def __init__(self, topic_arn: str, sqs_queue_url: str | None = None):
        self.topic_arn = topic_arn
        # Direct SQS write used locally because LocalStack community edition
        # does not reliably fan out SNS → SQS without a working queue policy.
        # The SNS publish still happens for production compatibility.
        self.sqs_queue_url = sqs_queue_url or os.environ.get("SQS_QUEUE_URL", "")
        self._sns = _boto("sns")
        self._sqs = _boto("sqs") if self.sqs_queue_url else None

    def publish(self, finding: dict) -> str | None:
        """Publish a finding to SNS (and directly to SQS for local watcher)."""
        payload = json.dumps(finding, indent=2)
        subject = f"[{finding.get('severity','unknown').upper()}] {finding.get('anomaly_type','anomaly')} detected"
        msg_id = None

        # 1. SNS publish (production path)
        try:
            resp = self._sns.publish(
                TopicArn=self.topic_arn,
                Message=payload,
                Subject=subject,
                MessageAttributes={
                    "severity":     {"DataType": "String", "StringValue": finding.get("severity", "unknown")},
                    "anomaly_type": {"DataType": "String", "StringValue": finding.get("anomaly_type", "unknown")},
                },
            )
            msg_id = resp["MessageId"]
            log.info("Published to SNS — MessageId: %s", msg_id)
        except Exception as exc:
            log.error("SNS publish error: %s", exc)

        # 2. Direct SQS write — watcher reads from here locally
        if self._sqs and self.sqs_queue_url:
            try:
                # Wrap in an SNS-envelope so the watcher parser stays the same
                envelope = {"Message": payload, "Subject": subject, "Type": "Notification"}
                self._sqs.send_message(
                    QueueUrl=self.sqs_queue_url,
                    MessageBody=json.dumps(envelope),
                )
                log.info("Wrote finding directly to SQS watcher queue")
            except Exception as exc:
                log.error("SQS direct write error: %s", exc)

        return msg_id
