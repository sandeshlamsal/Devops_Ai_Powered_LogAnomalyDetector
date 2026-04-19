import json
import logging
import os

import boto3

log = logging.getLogger(__name__)


def get_sns_client():
    return boto3.client(
        "sns",
        endpoint_url=os.environ.get("LOCALSTACK_ENDPOINT", "http://localhost:4566"),
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
    )


class SNSPublisher:
    def __init__(self, topic_arn: str):
        self.topic_arn = topic_arn
        self.client = get_sns_client()

    def publish(self, finding: dict) -> str | None:
        """Publish a finding dict to the SNS topic. Returns MessageId or None on error."""
        try:
            response = self.client.publish(
                TopicArn=self.topic_arn,
                Message=json.dumps(finding, indent=2),
                Subject=f"[{finding.get('severity', 'unknown').upper()}] {finding.get('anomaly_type', 'anomaly')} detected",
                MessageAttributes={
                    "severity": {
                        "DataType": "String",
                        "StringValue": finding.get("severity", "unknown"),
                    },
                    "anomaly_type": {
                        "DataType": "String",
                        "StringValue": finding.get("anomaly_type", "unknown"),
                    },
                },
            )
            msg_id = response["MessageId"]
            log.info("Published finding to SNS — MessageId: %s", msg_id)
            return msg_id
        except Exception as exc:
            log.error("SNS publish error: %s", exc)
            return None
