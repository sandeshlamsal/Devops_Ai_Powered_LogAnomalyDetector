import json
import logging
import os
import random
import signal
import time
from datetime import datetime, timezone

import boto3
import yaml

from log_templates import make_anomaly_burst, make_log

logging.basicConfig(level=logging.INFO, format="%(asctime)s [emitter] %(message)s")
log = logging.getLogger(__name__)

CONFIG_PATH = os.environ.get("CONFIG_PATH", "/config.yaml")


def load_config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        return {}


def get_cw_client():
    return boto3.client(
        "logs",
        endpoint_url=os.environ.get("LOCALSTACK_ENDPOINT", "http://localhost:4566"),
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
    )


def put_log_events(client, log_group: str, log_stream: str, events: list[dict]):
    timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    log_events = [
        {"timestamp": timestamp_ms, "message": json.dumps(event)}
        for event in events
    ]
    client.put_log_events(
        logGroupName=log_group,
        logStreamName=log_stream,
        logEvents=log_events,
    )


def pick_level(weights: dict) -> str:
    levels = list(weights.keys())
    probs = [weights[l] for l in levels]
    return random.choices(levels, weights=probs, k=1)[0]


def main():
    cfg = load_config()
    emitter_cfg = cfg.get("emitter", {})

    log_group = os.environ.get("LOG_GROUP", "/microservice/payment-service")
    log_stream = os.environ.get("LOG_STREAM", "application")
    interval = float(os.environ.get("EMIT_INTERVAL_SEC", emitter_cfg.get("interval_sec", 2)))
    level_weights = emitter_cfg.get("level_weights", {"info": 0.70, "warning": 0.20, "error": 0.10})
    anomaly_inject_interval = emitter_cfg.get("anomaly_inject_interval_sec", 60)

    client = get_cw_client()
    running = True
    last_anomaly_at = time.time()
    scenarios = ["cascading_timeout", "auth_failure_storm", "latency_spike"]
    scenario_idx = 0

    signal.signal(signal.SIGTERM, lambda *_: globals().update(running=False))

    log.info("Starting emitter → %s / %s  interval=%ss", log_group, log_stream, interval)

    while running:
        # Inject anomaly burst on schedule
        if anomaly_inject_interval > 0 and (time.time() - last_anomaly_at) >= anomaly_inject_interval:
            scenario = scenarios[scenario_idx % len(scenarios)]
            scenario_idx += 1
            burst = make_anomaly_burst(scenario)
            put_log_events(client, log_group, log_stream, burst)
            log.info("Injected anomaly burst: %s (%d events)", scenario, len(burst))
            last_anomaly_at = time.time()
        else:
            level = pick_level(level_weights)
            event = make_log(level.upper())
            put_log_events(client, log_group, log_stream, [event])
            log.debug("Emitted %s log", level)

        time.sleep(interval)


if __name__ == "__main__":
    main()
