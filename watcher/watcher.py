"""
Polls the SQS queue that is subscribed to the anomaly-findings SNS topic
and pretty-prints each finding to stdout in real time.
"""
import json
import logging
import os
import signal
import time

import boto3

logging.basicConfig(level=logging.INFO, format="%(asctime)s [watcher] %(message)s")
log = logging.getLogger(__name__)

SEVERITY_COLOR = {
    "low":      "\033[94m",   # blue
    "medium":   "\033[93m",   # yellow
    "high":     "\033[91m",   # red
    "critical": "\033[95m",   # magenta
}
RESET = "\033[0m"
BOLD  = "\033[1m"


def get_sqs_client():
    return boto3.client(
        "sqs",
        endpoint_url=os.environ.get("LOCALSTACK_ENDPOINT", "http://localhost:4566"),
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
    )


def resolve_queue_url(client) -> str:
    url = os.environ.get("SQS_QUEUE_URL", "")
    if url:
        return url
    # Derive URL from the configured endpoint so it works both in Docker and locally
    endpoint = os.environ.get("LOCALSTACK_ENDPOINT", "http://localhost:4566").rstrip("/")
    account = "000000000000"
    return f"{endpoint}/{account}/anomaly-findings-watcher"


def print_finding(finding: dict):
    severity = finding.get("severity", "unknown")
    color = SEVERITY_COLOR.get(severity, "")
    divider = "─" * 60

    print(f"\n{color}{BOLD}{divider}{RESET}")
    print(f"{color}{BOLD}  ANOMALY DETECTED{RESET}")
    print(f"{color}{BOLD}{divider}{RESET}")
    print(f"  {BOLD}Type:{RESET}     {finding.get('anomaly_type', 'n/a')}")
    print(f"  {BOLD}Severity:{RESET} {color}{severity.upper()}{RESET}")
    print(f"  {BOLD}Service:{RESET}  {finding.get('affected_service', 'n/a')}")
    print(f"\n  {BOLD}Root Cause:{RESET}")
    for line in (finding.get("root_cause_summary") or "").split(". "):
        if line:
            print(f"    {line.strip()}.")
    print(f"\n  {BOLD}Action:{RESET}")
    print(f"    {finding.get('recommended_action', 'n/a')}")
    print(f"{color}{divider}{RESET}\n")


def main():
    client = get_sqs_client()
    queue_url = resolve_queue_url(client)
    running = True
    signal.signal(signal.SIGTERM, lambda *_: globals().update(running=False))

    log.info("Watching for findings on queue: %s", queue_url)

    while running:
        try:
            response = client.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=10,   # long-poll — avoids hammering the queue
            )
        except Exception as exc:
            log.error("SQS receive error: %s — retrying in 5s", exc)
            time.sleep(5)
            continue

        for msg in response.get("Messages", []):
            try:
                # SNS wraps the payload in an envelope
                outer = json.loads(msg["Body"])
                payload = outer.get("Message", msg["Body"])
                finding = json.loads(payload)
                print_finding(finding)
            except (json.JSONDecodeError, KeyError) as exc:
                log.warning("Could not parse message: %s", exc)
            finally:
                client.delete_message(
                    QueueUrl=queue_url,
                    ReceiptHandle=msg["ReceiptHandle"],
                )


if __name__ == "__main__":
    main()
