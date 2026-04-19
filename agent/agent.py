import logging
import os
import signal
import time

import yaml

from cloudwatch_reader import CloudWatchReader
from claude_client import ClaudeClient
from sns_publisher import SNSPublisher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [agent] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

CONFIG_PATH = os.environ.get("CONFIG_PATH", "/config.yaml")

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def load_config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        return {}


def severity_meets_threshold(severity: str, threshold: str) -> bool:
    return SEVERITY_ORDER.get(severity, -1) >= SEVERITY_ORDER.get(threshold, 1)


def resolve_topic_arn() -> str:
    """Read SNS_TOPIC_ARN from env or the file written by the LocalStack init script."""
    arn = os.environ.get("SNS_TOPIC_ARN", "")
    if arn:
        return arn
    # Fallback: construct the default LocalStack ARN
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    return f"arn:aws:sns:{region}:000000000000:anomaly-findings"


def main():
    cfg = load_config()
    agent_cfg = cfg.get("agent", {})
    claude_cfg = cfg.get("claude", {})

    log_group = os.environ.get("LOG_GROUP", "/microservice/payment-service")
    log_stream = os.environ.get("LOG_STREAM", "application")
    poll_interval = float(os.environ.get("POLL_INTERVAL_SEC", agent_cfg.get("poll_interval_sec", 15)))
    batch_size = int(os.environ.get("LOG_BATCH_SIZE", agent_cfg.get("batch_size", 20)))
    min_severity = agent_cfg.get("min_publish_severity", "medium")

    model = claude_cfg.get("model", "claude-sonnet-4-6")
    max_tokens = claude_cfg.get("max_tokens", 512)
    retry_backoff = claude_cfg.get("retry_backoff_sec", [2, 4, 8])

    topic_arn = resolve_topic_arn()
    sqs_queue_url = os.environ.get("SQS_QUEUE_URL", "")

    reader = CloudWatchReader(log_group, log_stream)
    claude = ClaudeClient(model=model, max_tokens=max_tokens)
    publisher = SNSPublisher(topic_arn, sqs_queue_url=sqs_queue_url)

    running = True
    signal.signal(signal.SIGTERM, lambda *_: globals().update(running=False))

    log.info(
        "Agent started — polling %s/%s every %ss, batch=%d, min_severity=%s",
        log_group, log_stream, poll_interval, batch_size, min_severity,
    )
    log.info("SNS topic: %s", topic_arn)

    buffer: list[str] = []

    while running:
        new_logs = reader.fetch(limit=batch_size * 2)
        if new_logs:
            buffer.extend(new_logs)
            log.info("Buffered %d new log lines (total buffered: %d)", len(new_logs), len(buffer))

        while len(buffer) >= batch_size:
            batch, buffer = buffer[:batch_size], buffer[batch_size:]
            log.info("Analyzing batch of %d lines...", len(batch))

            finding = claude.analyze(batch, retry_backoff=retry_backoff)

            if finding.get("anomaly_detected"):
                severity = finding.get("severity", "unknown")
                anomaly_type = finding.get("anomaly_type", "unknown")
                log.warning(
                    "Anomaly detected — type=%s severity=%s service=%s",
                    anomaly_type, severity, finding.get("affected_service"),
                )
                log.warning("Root cause: %s", finding.get("root_cause_summary"))

                if severity_meets_threshold(severity, min_severity):
                    publisher.publish(finding)
                else:
                    log.info("Severity %s below threshold %s — skipping SNS publish", severity, min_severity)
            else:
                log.info("No anomaly detected in batch.")

        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
