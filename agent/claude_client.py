import json
import logging
import os
import random
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

# Mock responses used when CLAUDE_MOCK=true — exercises the full pipeline without a real API key
_MOCK_ANOMALIES = [
    {
        "anomaly_detected": True,
        "severity": "high",
        "anomaly_type": "cascading_timeout",
        "affected_service": "payment-service",
        "root_cause_summary": (
            "[MOCK] Multiple consecutive database connection timeouts detected "
            "(latency 4800-5500ms). The pattern suggests the upstream DB connection "
            "pool is exhausted and requests are failing after max retries."
        ),
        "recommended_action": "Check DB connection pool limits. Scale pool size or shed load until DB recovers.",
    },
    {
        "anomaly_detected": True,
        "severity": "high",
        "anomaly_type": "auth_failure_storm",
        "affected_service": "auth-service",
        "root_cause_summary": (
            "[MOCK] Burst of 401 authentication failures across multiple request IDs "
            "within a 30-second window. Likely cause is an expired or rotated API key "
            "that has not been propagated to all service instances."
        ),
        "recommended_action": "Rotate and re-deploy credentials. Check key expiry in secrets manager.",
    },
    {
        "anomaly_detected": True,
        "severity": "medium",
        "anomaly_type": "latency_spike",
        "affected_service": "order-service",
        "root_cause_summary": (
            "[MOCK] Request latency spiked to 5000-9000ms on order endpoints while "
            "HTTP status codes remained 200. Suggests a slow downstream dependency "
            "rather than an outright failure."
        ),
        "recommended_action": "Profile downstream calls from order-service. Check inventory-service response times.",
    },
    {
        "anomaly_detected": False,
        "severity": None,
        "anomaly_type": None,
        "affected_service": None,
        "root_cause_summary": None,
        "recommended_action": None,
    },
]


def _mock_analyze(log_batch: list[str]) -> dict:
    """Return a realistic mock finding based on log batch content."""
    joined = " ".join(log_batch).lower()
    if "timeout" in joined or "connectiontimeout" in joined:
        finding = _MOCK_ANOMALIES[0]
    elif "invalidtoken" in joined or "401" in joined:
        finding = _MOCK_ANOMALIES[1]
    elif "latency" in joined and "5000" in joined:
        finding = _MOCK_ANOMALIES[2]
    else:
        # 40% chance of anomaly on generic batches to keep the demo interesting
        finding = random.choices(_MOCK_ANOMALIES[:3] + [_MOCK_ANOMALIES[3]], weights=[1, 1, 1, 4])[0]

    log.info("[MOCK] Returning mock finding: anomaly_detected=%s type=%s",
             finding["anomaly_detected"], finding.get("anomaly_type"))
    return finding


class ClaudeClient:
    def __init__(self, model: str = "claude-sonnet-4-6", max_tokens: int = 512):
        self.model = model
        self.max_tokens = max_tokens
        self.mock = os.environ.get("CLAUDE_MOCK", "").lower() in ("1", "true", "yes")

        if self.mock:
            log.warning("CLAUDE_MOCK=true — using mock responses, no real API calls will be made")
            self.client = None
        else:
            self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    def analyze(self, log_batch: list[str], retry_backoff: list[int] | None = None) -> dict:
        """Send a batch of raw log strings to Claude and return a parsed finding dict."""
        if self.mock:
            return _mock_analyze(log_batch)

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
                log.info(
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
