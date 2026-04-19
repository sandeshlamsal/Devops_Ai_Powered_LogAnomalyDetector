import json
import logging
import os
import time

import anthropic

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a site reliability engineer specializing in microservice log analysis.

You will receive a batch of structured JSON log lines from a microservice. Your job is to detect anomalies.

Classify findings using ONLY these anomaly types:
  error_rate_spike         - sudden surge in ERROR-level lines above baseline
  latency_spike            - request latency far above normal without hard failure
  cascading_timeout        - chain of timeouts propagating across calls
  connection_pool_exhaustion - service unable to acquire DB or HTTP connections
  auth_failure_storm       - burst of 401/403 rejections from multiple request IDs
  dependency_degradation   - downstream service returning degraded/partial responses
  resource_exhaustion      - OOM, GC pressure, file descriptor or disk pressure
  data_validation_failure  - JSON parse errors, null pointers, schema mismatches
  traffic_anomaly          - request volume deviates significantly from expected
  repeated_retry_storm     - same operation retried many times flooding logs

Severity levels:
  low      - isolated, self-resolving, no user impact
  medium   - degraded performance, elevated error rate, service still functional
  high     - significant user-facing impact, requires prompt attention
  critical - service down or data integrity at risk

Respond ONLY with valid JSON matching this exact schema:
{
  "anomaly_detected": <bool>,
  "severity": "<low|medium|high|critical>",
  "anomaly_type": "<one of the types above, or null if none>",
  "affected_service": "<service name from logs, or null>",
  "root_cause_summary": "<2-3 sentence explanation, or null>",
  "recommended_action": "<concrete next step for on-call engineer, or null>"
}
If no anomaly is detected set anomaly_detected to false and all other fields to null.
Do not include any text outside the JSON object."""


class ClaudeClient:
    def __init__(self, model: str = "claude-sonnet-4-6", max_tokens: int = 512):
        self.model = model
        self.max_tokens = max_tokens
        self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    def analyze(self, log_batch: list[str], retry_backoff: list[int] | None = None) -> dict:
        """Send a batch of raw log strings to Claude and return a parsed finding dict."""
        if retry_backoff is None:
            retry_backoff = [2, 4, 8]

        user_content = "Analyze this log batch for anomalies:\n\n" + "\n".join(log_batch)

        for attempt, wait in enumerate([0] + retry_backoff):
            if wait:
                time.sleep(wait)
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=[
                        {
                            "type": "text",
                            "text": SYSTEM_PROMPT,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": user_content}],
                )
                raw = response.content[0].text.strip()
                finding = json.loads(raw)
                log.debug(
                    "Claude usage — input: %d, output: %d, cache_read: %d",
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                    getattr(response.usage, "cache_read_input_tokens", 0),
                )
                return finding
            except json.JSONDecodeError as exc:
                log.error("Claude returned non-JSON (attempt %d): %s", attempt + 1, exc)
            except anthropic.APIError as exc:
                log.error("Claude API error (attempt %d): %s", attempt + 1, exc)
                if attempt == len(retry_backoff):
                    raise

        return {"anomaly_detected": False}
