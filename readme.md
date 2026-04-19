# AI-Powered Log Anomaly Detector

Synthetic microservice emits structured logs to LocalStack CloudWatch. A Python agent reads log streams and uses Claude API to classify anomalies, summarize root cause, and post findings to SNS (also mocked).

---

## Overview

This project simulates a production-like observability pipeline entirely on a local machine using LocalStack. A synthetic microservice continuously emits structured JSON logs (info, warning, error events) to AWS CloudWatch Logs. A Python-based anomaly detection agent polls those log streams, feeds batches of logs to the Claude API for AI-powered analysis, and publishes actionable findings (anomaly classification + root cause summary) to an SNS topic — all without touching real AWS infrastructure.

**Key goals:**
- Demonstrate AI-augmented log analysis using Claude as the reasoning engine
- Show a realistic DevOps observability pattern using AWS services (CloudWatch, SNS) locally via LocalStack
- Keep the entire stack runnable with a single `docker compose up`

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Local Machine                         │
│                                                             │
│  ┌──────────────────┐        ┌──────────────────────────┐  │
│  │  Microservice    │        │       LocalStack          │  │
│  │  (log emitter)   │──────▶│                           │  │
│  │                  │  logs  │  ┌─────────────────────┐ │  │
│  │  Python script   │        │  │  CloudWatch Logs    │ │  │
│  │  emits JSON logs │        │  │  (log groups/streams│ │  │
│  │  every N seconds │        │  └──────────┬──────────┘ │  │
│  └──────────────────┘        │             │             │  │
│                              │  ┌──────────▼──────────┐ │  │
│  ┌──────────────────┐        │  │  SNS Topic          │ │  │
│  │  Anomaly Agent   │──────▶│  │  (findings output)  │ │  │
│  │                  │publish │  └─────────────────────┘ │  │
│  │  - Polls CW logs │        └──────────────────────────┘  │
│  │  - Batches logs  │                                       │
│  │  - Sends to      │        ┌──────────────────────────┐  │
│  │    Claude API    │──────▶│      Claude API           │  │
│  │  - Parses AI     │◀──────│  (anomaly classification  │  │
│  │    response      │ result │   + root cause summary)   │  │
│  └──────────────────┘        └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| AWS simulation | LocalStack (CloudWatch Logs, SNS) |
| Log emitter | Python 3.11+ with `boto3` |
| Anomaly agent | Python 3.11+ with `boto3` + Anthropic SDK |
| AI model | Claude Sonnet 4.6 (`claude-sonnet-4-6`) |
| Containerization | Docker + Docker Compose |
| Configuration | `.env` file + `config.yaml` |

---

## Project Structure

```
.
├── docker-compose.yml            # Spins up LocalStack + emitter + agent + watcher
├── .env.example                  # Environment variable template
├── .gitignore
├── config.yaml                   # Tunable parameters (poll interval, batch size)
├── localstack/
│   └── init-aws.sh               # Bootstrap CW log group, SNS topic, SQS queue + subscription
├── emitter/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── emitter.py                # Synthetic log generator
│   └── log_templates.py          # Structured log event definitions + anomaly burst scenarios
├── agent/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── agent.py                  # Main polling + analysis loop
│   ├── cloudwatch_reader.py      # CW Logs nextForwardToken cursor wrapper
│   ├── claude_client.py          # Claude API interaction (taxonomy prompt + caching)
│   └── sns_publisher.py          # SNS publish wrapper
├── watcher/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── watcher.py                # SQS long-poll consumer, color-coded terminal output
└── readme.md
```

---

## Components

### 1. Log Emitter (`emitter/emitter.py`)

Simulates a microservice by emitting structured JSON logs on a configurable interval. Produces a realistic mix of log levels:

- `INFO` — normal request completions (HTTP 200, latency within bounds)
- `WARNING` — elevated latency, retries, degraded responses
- `ERROR` — failed requests, exceptions, timeouts, 5xx responses

Each log line is a JSON object:

```json
{
  "timestamp": "2026-04-19T10:00:00Z",
  "level": "ERROR",
  "service": "payment-service",
  "request_id": "a3f9...",
  "message": "Database connection timeout after 3 retries",
  "latency_ms": 4820,
  "status_code": 503
}
```

Logs are written to a CloudWatch log stream under a configurable log group.

### 2. Anomaly Agent (`agent/agent.py`)

Runs in a continuous poll loop:

1. **Read** — fetches the latest N log lines from CloudWatch since the last checkpoint
2. **Batch** — groups logs into sliding windows (configurable size/overlap)
3. **Analyze** — sends each batch to Claude with a structured prompt requesting anomaly classification and root cause
4. **Publish** — posts findings to SNS topic as a JSON message

The agent maintains a `last_token` cursor so it never re-processes the same logs.

### 3. Claude Client (`agent/claude_client.py`)

Wraps the Anthropic SDK with a focused prompt and structured output contract:

**Prompt strategy:**
- System prompt defines the agent as a log analysis expert **and embeds the full anomaly taxonomy** (see below)
- User message provides raw log batch as JSON
- Requests output in a strict JSON schema: `{ "anomaly_detected": bool, "severity": "low|medium|high|critical", "anomaly_type": str, "affected_service": str, "root_cause_summary": str, "recommended_action": str }`

**Claude model:** `claude-sonnet-4-6` with prompt caching on the system prompt to reduce cost on repeated calls.

---

## Anomaly Taxonomy

The **anomaly taxonomy** is a structured classification system embedded in the Claude system prompt. It gives Claude a fixed vocabulary of anomaly types and severity rules so every finding uses a consistent label — instead of Claude inventing a different description each run.

Without a taxonomy, Claude might call the same problem `"db_timeout"`, `"database_connection_failure"`, or `"upstream_timeout"` across different batches. With the taxonomy defined up front, it always picks from the agreed list and downstream consumers (SNS subscribers, dashboards, alerting rules) can filter on stable values.

### Anomaly Types

| `anomaly_type` value | Description | Typical signals in logs |
|---|---|---|
| `error_rate_spike` | Sudden surge in ERROR-level log lines above baseline | ERROR count > 10% of batch, sustained across multiple request IDs |
| `latency_spike` | Request latency exceeds normal threshold without a hard failure | `latency_ms` > 2000ms on otherwise successful (2xx) responses |
| `cascading_timeout` | Chain of timeouts propagating across service calls | Sequential timeout messages with escalating retry counts |
| `connection_pool_exhaustion` | Service unable to acquire DB or HTTP client connections | "pool exhausted", "max connections reached", connection wait > threshold |
| `auth_failure_storm` | Burst of authentication or authorization rejections | 401/403 status codes from multiple distinct request IDs in short window |
| `dependency_degradation` | Downstream service returning degraded/partial responses | 206, 207, or custom degraded-mode status codes; increased error rate on one upstream |
| `resource_exhaustion` | CPU, memory, file descriptor, or disk pressure visible in logs | OOM messages, GC pressure logs, "too many open files" errors |
| `data_validation_failure` | Unexpected payload shapes or schema mismatches | JSON parse errors, null pointer exceptions, type mismatch messages |
| `traffic_anomaly` | Request volume deviates significantly from expected pattern | Abnormally high or near-zero log density within the batch window |
| `repeated_retry_storm` | A single operation retried many times, flooding logs | Same `request_id` or operation appearing > N times with retry indicators |

### Severity Levels

| Level | Meaning | Example condition |
|---|---|---|
| `low` | Isolated, self-resolving, no user impact | Single timeout that succeeded on retry |
| `medium` | Degraded performance or elevated error rate but service still functional | Latency 2–5× normal; error rate 5–15% |
| `high` | Significant user-facing impact; requires prompt attention | Error rate > 15%; repeated cascading failures |
| `critical` | Service is down or data integrity is at risk | 100% error rate; OOM crash; connection pool fully exhausted |

### How the Taxonomy Appears in the System Prompt

The system prompt passed to Claude looks like this (simplified):

```
You are a site reliability engineer specializing in microservice log analysis.

Classify each log batch using ONLY the following anomaly types:
  error_rate_spike, latency_spike, cascading_timeout,
  connection_pool_exhaustion, auth_failure_storm,
  dependency_degradation, resource_exhaustion,
  data_validation_failure, traffic_anomaly, repeated_retry_storm

Assign severity using these thresholds:
  low    → isolated, self-resolving
  medium → degraded but functional
  high   → significant user impact
  critical → service down or data at risk

Respond ONLY with valid JSON matching this schema:
{
  "anomaly_detected": <bool>,
  "severity": "<low|medium|high|critical>",
  "anomaly_type": "<one of the types above>",
  "affected_service": "<service name from logs>",
  "root_cause_summary": "<2-3 sentence explanation>",
  "recommended_action": "<concrete next step for on-call engineer>"
}
If no anomaly is detected, set anomaly_detected to false and omit the other fields.
```

This prompt is **cached** via the Anthropic prompt caching feature — because the system prompt (which contains the full taxonomy) is the same on every call, it is stored server-side for up to 5 minutes, cutting the input token cost by ~90% for the taxonomy portion.

### 4. SNS Publisher (`agent/sns_publisher.py`)

Publishes findings to a LocalStack SNS topic. In a real deployment this topic could fan out to PagerDuty, Slack, email, or an incident management system. Locally, published messages are readable via the LocalStack web UI or CLI.

### 5. Watcher (`watcher/watcher.py`)

A lightweight SQS consumer that runs as a fourth container and prints every anomaly finding to stdout in color-coded format as it arrives — no extra tooling needed to see results.

```
──────────────────────────────────────────────────────────────
  ANOMALY DETECTED
──────────────────────────────────────────────────────────────
  Type:     cascading_timeout
  Severity: HIGH
  Service:  payment-service

  Root Cause:
    Three consecutive DB timeouts (4800–5200ms) suggest the
    connection pool is exhausted.

  Action:
    Check DB pool limits and active connections. Consider
    shedding load until DB recovers.
──────────────────────────────────────────────────────────────
```

Color coding: blue = low, yellow = medium, red = high, magenta = critical.

The watcher uses **SQS long-polling** (`WaitTimeSeconds=10`) so it reacts within seconds of a finding being published, with near-zero CPU idle cost.

### 6. LocalStack Init (`localstack/init-aws.sh`)

Shell script executed at LocalStack startup that pre-creates:
- CloudWatch log group: `/microservice/payment-service`
- CloudWatch log stream: `application`
- SNS topic: `anomaly-findings`
- SQS queue: `anomaly-findings-watcher` (subscribed to the SNS topic)

---

## Setup

### Prerequisites

- Docker Desktop (with Compose v2)
- Anthropic API key

### Quick Start

```bash
# 1. Clone and enter the project
git clone <repo-url>
cd Devops_Ai_Powered_LogAnomalyDetector

# 2. Configure environment
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=sk-ant-...

# 3. Start the full stack
docker compose up --build

# 4. Watch findings
# In a second terminal:
aws --endpoint-url=http://localhost:4566 sns list-subscriptions
```

### Environment Variables (`.env`)

| Variable | Description | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key | required |
| `AWS_DEFAULT_REGION` | LocalStack region | `us-east-1` |
| `LOCALSTACK_ENDPOINT` | LocalStack URL | `http://localstack:4566` |
| `LOG_GROUP` | CloudWatch log group name | `/microservice/payment-service` |
| `SNS_TOPIC_ARN` | SNS topic ARN | auto-set by init script |
| `EMIT_INTERVAL_SEC` | Seconds between emitted logs | `2` |
| `POLL_INTERVAL_SEC` | Agent poll frequency in seconds | `15` |
| `LOG_BATCH_SIZE` | Logs per Claude call | `20` |

---

## How It Works — Step by Step

1. `docker compose up` starts three containers: `localstack`, `emitter`, `agent`
2. LocalStack `init-aws.sh` creates the log group, stream, and SNS topic
3. The **emitter** begins writing JSON log events to CloudWatch every `EMIT_INTERVAL_SEC` seconds, randomly injecting anomaly scenarios (burst of errors, latency spikes, cascading failures)
4. The **agent** wakes every `POLL_INTERVAL_SEC` seconds, fetches new log lines since its last cursor position
5. Logs are batched and sent to **Claude** with a system prompt that defines the anomaly taxonomy
6. Claude returns a structured JSON finding
7. If `anomaly_detected` is `true`, the agent publishes the finding to **SNS**
8. Findings accumulate in the SNS topic; a subscriber (email, Lambda, Slack webhook) would consume them in production

---

## Example Claude Output

```json
{
  "anomaly_detected": true,
  "severity": "high",
  "anomaly_type": "cascading_timeout",
  "affected_service": "payment-service",
  "root_cause_summary": "Three consecutive database connection timeouts (latency 4800-5200ms) following a spike in ERROR-level logs suggest the upstream DB connection pool is exhausted. The pattern escalated from isolated retries to total request failures within 90 seconds.",
  "recommended_action": "Check DB connection pool limits and active connections. Consider scaling the pool or shedding load on the payment-service until DB recovers."
}
```

---

## Implementation Phases

### Phase 1 — Infrastructure Scaffold
- [x] `docker-compose.yml` with LocalStack, emitter, agent, watcher services
- [x] `localstack/init-aws.sh` creates log group, stream, SNS topic, SQS queue + subscription
- [x] `.env.example` with all required variables

### Phase 2 — Log Emitter
- [x] `emitter.py` emitting structured JSON logs on a timer
- [x] `log_templates.py` with realistic INFO/WARN/ERROR templates
- [x] Injected anomaly scenarios: DB timeout burst, latency spike, auth failure storm

### Phase 3 — CloudWatch Reader
- [x] `cloudwatch_reader.py` wrapping `get_log_events` with `nextForwardToken` cursor
- [x] Cursor advances only on non-empty responses to avoid skipping real events

### Phase 4 — Claude Integration
- [x] `claude_client.py` with system prompt, user message builder, JSON response parser
- [x] Prompt caching on system prompt
- [x] Retry logic with exponential backoff on API errors

### Phase 5 — SNS Publisher & Agent Loop
- [x] `sns_publisher.py` publishing finding JSON to LocalStack SNS
- [x] `agent.py` main loop wiring reader → Claude → publisher
- [x] Graceful shutdown on SIGTERM

### Phase 6 — Observability & Polish
- [x] Structured logging for the agent itself
- [x] `config.yaml` for tunable parameters without rebuilding images
- [x] SQS watcher service — color-coded terminal output of live findings
- [x] `.gitignore` protecting `.env` from being committed

---

## Local Development (without Docker)

```bash
# Install dependencies
pip install boto3 anthropic pyyaml

# Point boto3 at LocalStack
export AWS_DEFAULT_REGION=us-east-1
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test

# Run emitter
python emitter/emitter.py

# Run agent (in a second terminal)
python agent/agent.py
```

---

## Cost Considerations

- The agent uses **prompt caching** on the system prompt — repeated calls within 5 minutes reuse the cached prefix, cutting input token cost by ~90% for the system portion.
- With `LOG_BATCH_SIZE=20` and `POLL_INTERVAL_SEC=15`, expect roughly 4 Claude calls/minute during active anomaly periods.
- Use `claude-haiku-4-5-20251001` instead of Sonnet for lower cost during development.
