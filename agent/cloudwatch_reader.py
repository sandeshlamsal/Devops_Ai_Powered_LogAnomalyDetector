import logging
import os

import boto3

log = logging.getLogger(__name__)


def get_cw_client():
    return boto3.client(
        "logs",
        endpoint_url=os.environ.get("LOCALSTACK_ENDPOINT", "http://localhost:4566"),
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
    )


class CloudWatchReader:
    """
    Reads log events from a single CloudWatch log stream, advancing a cursor
    (nextToken) so each call only returns events since the last read.
    """

    def __init__(self, log_group: str, log_stream: str):
        self.log_group = log_group
        self.log_stream = log_stream
        self.client = get_cw_client()
        self._next_token: str | None = None

    def fetch(self, limit: int = 100) -> list[str]:
        """Return up to `limit` new raw log message strings since the last call."""
        kwargs: dict = {
            "logGroupName": self.log_group,
            "logStreamName": self.log_stream,
            "startFromHead": False,
            "limit": limit,
        }
        if self._next_token:
            kwargs["nextToken"] = self._next_token

        messages = []
        try:
            response = self.client.get_log_events(**kwargs)
            events = response.get("events", [])
            messages = [e["message"] for e in events]
            new_token = response.get("nextForwardToken")
            # Only advance the cursor if there were actual new events.
            # LocalStack returns the same token when the stream is at the end,
            # so advancing on an empty response would skip real events.
            if events:
                self._next_token = new_token
            log.debug("Fetched %d events from %s/%s", len(messages), self.log_group, self.log_stream)
        except self.client.exceptions.ResourceNotFoundException:
            log.warning("Log stream %s/%s not found — will retry", self.log_group, self.log_stream)
        except Exception as exc:
            log.error("CloudWatch read error: %s", exc)

        return messages
