# AI-Powered Log Anomaly Detector

Synthetic microservice emits structured logs to LocalStack CloudWatch. A Python agent reads log streams and uses Claude API to classify anomalies, summarize root cause, and post findings to SNS (also mocked).

---

## Overview

This project simulates a production-like observability pipeline entirely on a local machine using LocalStack. A synthetic microservice continuously emits structured JSON logs (info, warning, error events) to AWS CloudWatch Logs. A Python-based anomaly detection agent polls those log streams, feeds batches of logs to the Claude API for AI-powered analysis, and publishes actionable findings (anomaly classification + root cause summary) to an SNS topic — all without touching real AWS infrastructure.

**Key goals:**
- Demonstrate AI-augmented log analysis using Claude as the reasoning engine
- Show a realistic DevOps observability pattern using AWS services (CloudWatch, SNS, SQS) locally via LocalStack
- Keep the entire stack runnable with a single `docker compose up --build`

---

## Current Status

**Stack: fully operational as of 2026-04-19**

All 4 containers run and pass health checks:

| Container | Status | Role |
|---|---|---|
| `localstack-1` | Healthy | Mocks CloudWatch Logs, SNS, SQS |
| `emitter-1` | Running | Writes JSON logs every 2s, bursts anomalies every 60s |
| `agent-1` | Running | Polls CW logs, calls Claude, publishes findings to SNS |
| `watcher-1` | Running | Consumes SQS queue, prints color-coded findings |

Verified behavior:
- LocalStack init script creates log group, stream, SNS topic, and SQS queue on startup
- Emitter writes logs to CloudWatch; agent picks them up within the poll interval
- Claude API calls succeed with prompt caching active
- Anomaly findings published to SNS and consumed by watcher within seconds

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         Docker Compose (local)                    │
│                                                                  │
│  ┌─────────────────┐      ┌──────────────────────────────────┐  │
│  │    emitter      │      │           localstack              │  │
│  │                 │─────▶│                                   │  │
│  │  Writes JSON    │ logs  │  ┌─────────────────────────────┐ │  │
│  │  logs every 2s  │      │  │  CloudWatch Logs             │ │  │
│  │  Injects burst  │      │  │  /microservice/payment-svc   │ │  │
│  │  anomalies      │      │  └──────────────┬──────────────┘ │  │
│  │  every 60s      │      │                 │                 │  │
│  └─────────────────┘      │  ┌──────────────▼──────────────┐ │  │
│                           │  │  SNS: anomaly-findings       │ │  │
│  ┌─────────────────┐      │  └──────────────┬──────────────┘ │  │
│  │     agent       │─────▶│                 │                 │  │
│  │                 │pub   │  ┌──────────────▼──────────────┐ │  │
│  │  Polls CW logs  │      │  │  SQS: anomaly-findings-     │ │  │
│  │  Batches logs   │      │  │        watcher              │ │  │
│  │  Calls Claude   │      │  └──────────────┬──────────────┘ │  │
│  │  Publishes SNS  │      └─────────────────┼────────────────┘  │
│  └────────┬────────┘                        │                    │
│           │                    ┌────────────▼────────┐          │
│           │ analyze            │       watcher        │          │
│           ▼                    │                      │          │
│  ┌─────────────────┐           │  Polls SQS           │          │
│  │   Claude API    │           │  Prints findings     │          │
│  │  claude-sonnet  │           │  Color-coded stdout  │          │
│  │  Taxonomy prompt│           └──────────────────────┘          │
│  │  + caching      │                                              │
│  └─────────────────┘                                              │
└──────────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| AWS simulation | LocalStack 3.4 (CloudWatch Logs, SNS, SQS) |
| Log emitter | Python 3.11 + `boto3` |
| Anomaly agent | Python 3.11 + `boto3` + Anthropic SDK 0.40 |
| AI model | Claude Sonnet 4.6 (`claude-sonnet-4-6`) |
| Containerization | Docker + Docker Compose v2 |
| Configuration | `.env` file + `config.yaml` (baked into images at build time) |

---

## Project Structure

```
.
├── docker-compose.yml            # 4-service compose: localstack, emitter, agent, watcher
├── .env.example                  # Environment variable template
├── .gitignore                    # Protects .env from commits
├── config.yaml                   # Tunable parameters (baked into images at build time)
├── localstack/
│   ├── Dockerfile                # Custom LocalStack image with init script baked in
│   └── init-aws.sh               # Creates CW log group, SNS topic, SQS queue + subscription
├── emitter/
│   ├── Dockerfile                # Uses project root as build context to access config.yaml
│   ├── requirements.txt
│   ├── emitter.py                # Timed log emitter with anomaly burst injection
│   └── log_templates.py          # INFO/WARN/ERROR templates + 3 anomaly burst scenarios
├── agent/
│   ├── Dockerfile                # Uses project root as build context to access config.yaml
│   ├── requirements.txt
│   ├── agent.py                  # Main polling + analysis loop
│   ├── cloudwatch_reader.py      # CW Logs nextForwardToken cursor wrapper
│   ├── claude_client.py          # Claude API call with taxonomy prompt + prompt caching
│   └── sns_publisher.py          # Publishes findings to SNS with severity/type attributes
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
  "level": "ERROR",
  "service": "payment-service",
  "request_id": "a3f9...",
  "message": "Database connection timeout after 3 retries",
  "latency_ms": 4820,
  "status_code": 503
}
```

Every 60 seconds the emitter injects a **burst anomaly scenario** — a tight cluster of 8 related error logs — to reliably trigger anomaly detection. Three scenarios cycle in rotation: `cascading_timeout`, `auth_failure_storm`, `latency_spike`.

### 2. Anomaly Agent (`agent/agent.py`)

Runs in a continuous poll loop:

1. **Read** — fetches the latest N log lines from CloudWatch since the last `nextForwardToken` cursor
2. **Buffer** — accumulates logs until a full batch is available
3. **Analyze** — sends each batch to Claude with a structured prompt requesting anomaly classification and root cause
4. **Publish** — posts findings meeting the minimum severity threshold to SNS

### 3. Claude Client (`agent/claude_client.py`)

Wraps the Anthropic SDK with a focused prompt and structured output contract:

**Prompt strategy:**
- System prompt defines the agent as a log analysis expert **and embeds the full anomaly taxonomy** (see below)
- User message provides raw log batch as JSON
- Requests output in a strict JSON schema: `{ "anomaly_detected": bool, "severity": "low|medium|high|critical", "anomaly_type": str, "affected_service": str, "root_cause_summary": str, "recommended_action": str }`
- Retry logic: up to 3 retries with exponential backoff (2s, 4s, 8s)

**Claude model:** `claude-sonnet-4-6` with `cache_control: ephemeral` on the system prompt — the taxonomy is large and static, so caching reduces input token cost by ~90% on repeated calls within 5 minutes.

### 4. SNS Publisher (`agent/sns_publisher.py`)

Publishes findings to a LocalStack SNS topic with `severity` and `anomaly_type` as SNS message attributes — enabling downstream consumers to filter by severity without parsing the full payload. In production this topic fans out to PagerDuty, Slack, email, or an incident management system.

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

Uses **SQS long-polling** (`WaitTimeSeconds=10`) for near-zero CPU idle cost.

### 6. LocalStack Init (`localstack/init-aws.sh`)

Shell script executed at LocalStack startup (via the `/etc/localstack/init/ready.d/` hook) that pre-creates:
- CloudWatch log group: `/microservice/payment-service`
- CloudWatch log stream: `application`
- SNS topic: `anomaly-findings`
- SQS queue: `anomaly-findings-watcher`
- SNS → SQS subscription (so every SNS publish lands in the queue)

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

```
You are a site reliability engineer specializing in microservice log analysis.

Classify each log batch using ONLY the following anomaly types:
  error_rate_spike, latency_spike, cascading_timeout,
  connection_pool_exhaustion, auth_failure_storm,
  dependency_degradation, resource_exhaustion,
  data_validation_failure, traffic_anomaly, repeated_retry_storm

Assign severity using these thresholds:
  low      → isolated, self-resolving
  medium   → degraded but functional
  high     → significant user impact
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
```

This prompt is **cached** using `cache_control: ephemeral` — reducing input token cost by ~90% for the taxonomy portion on repeated calls within 5 minutes.

---

## Setup

### Prerequisites

- Docker Desktop (Compose v2) — see [Docker notes](#docker-desktop-notes-intel-mac) if on Intel Mac
- Anthropic API key

### Quick Start

```bash
# 1. Clone and enter the project
git clone https://github.com/sandeshlamsal/Devops_Ai_Powered_LogAnomalyDetector.git
cd Devops_Ai_Powered_LogAnomalyDetector

# 2. Configure environment
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY=sk-ant-...

# 3. Start the full stack
docker compose up --build

# 4. Watch live anomaly findings (second terminal)
docker compose logs -f watcher
```

Anomaly bursts are injected every 60 seconds. You will see color-coded findings in the watcher logs within ~75 seconds of startup.

### Environment Variables (`.env`)

| Variable | Description | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key | required |
| `AWS_DEFAULT_REGION` | LocalStack region | `us-east-1` |
| `LOG_GROUP` | CloudWatch log group name | `/microservice/payment-service` |
| `LOG_STREAM` | CloudWatch log stream name | `application` |
| `EMIT_INTERVAL_SEC` | Seconds between emitted logs | `2` |
| `POLL_INTERVAL_SEC` | Agent poll frequency in seconds | `15` |
| `LOG_BATCH_SIZE` | Logs per Claude call | `20` |

---

## How It Works — Step by Step

1. `docker compose up --build` starts **4 containers**: `localstack`, `emitter`, `agent`, `watcher`
2. LocalStack `init-aws.sh` runs at startup and creates the CloudWatch log group, stream, SNS topic, SQS queue, and SNS→SQS subscription
3. The **emitter** writes structured JSON log events to CloudWatch every 2 seconds, cycling through INFO/WARN/ERROR. Every 60 seconds it injects a burst anomaly scenario (8 tightly-clustered error logs)
4. The **agent** polls CloudWatch every 15 seconds using a `nextForwardToken` cursor to read only new events since the last check
5. When the buffer reaches `LOG_BATCH_SIZE` (20) logs, the agent sends the batch to **Claude** with the taxonomy-embedded system prompt
6. Claude returns a structured JSON finding
7. If `anomaly_detected` is `true` and severity meets the threshold, the agent publishes the finding to **SNS**
8. SNS fans the message to the SQS queue
9. The **watcher** long-polls SQS (up to 10s wait) and immediately prints the finding with color-coded severity formatting

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

### Phase 1 — Infrastructure Scaffold ✅
- [x] `docker-compose.yml` with LocalStack, emitter, agent, watcher services
- [x] `localstack/init-aws.sh` creates log group, stream, SNS topic, SQS queue + subscription
- [x] `.env.example` with all required variables

### Phase 2 — Log Emitter ✅
- [x] `emitter.py` emitting structured JSON logs on a timer
- [x] `log_templates.py` with realistic INFO/WARN/ERROR templates
- [x] Injected anomaly scenarios: DB timeout burst, latency spike, auth failure storm

### Phase 3 — CloudWatch Reader ✅
- [x] `cloudwatch_reader.py` wrapping `get_log_events` with `nextForwardToken` cursor
- [x] Cursor advances only on non-empty responses to avoid skipping real events

### Phase 4 — Claude Integration ✅
- [x] `claude_client.py` with system prompt, user message builder, JSON response parser
- [x] Prompt caching on system prompt (`cache_control: ephemeral`)
- [x] Retry logic with exponential backoff on API errors

### Phase 5 — SNS Publisher & Agent Loop ✅
- [x] `sns_publisher.py` publishing finding JSON to LocalStack SNS with message attributes
- [x] `agent.py` main loop wiring reader → Claude → publisher
- [x] Graceful shutdown on SIGTERM

### Phase 6 — Observability & Polish ✅
- [x] Structured logging for the agent itself
- [x] `config.yaml` for tunable parameters, baked into images at build time
- [x] SQS watcher service — color-coded terminal output of live findings
- [x] `.gitignore` protecting `.env` from being committed

---

## Issues Encountered & Solutions

### Issue 1 — Docker context pointing to dead socket

**Symptom:** `docker compose up` failed with:
```
failed to connect to the docker API at unix:///Users/sandeshlamsal/.docker/run/docker.sock
```

**Root cause:** The active Docker context (`desktop-linux`) pointed to a socket that didn't exist because Docker Desktop was not running. Multiple container runtimes were installed (Docker Desktop, Colima, Rancher Desktop) and only Colima had a socket file — but it was stale (process not running).

**Solution:** Started Docker Desktop from the Applications folder and switched the active context:
```bash
docker context use desktop-linux
```

---

### Issue 2 — HyperKit crash on Intel Mac (persistent)

**Symptom:** Docker Desktop kept failing with:
```
hyperkit: process terminated unexpectedly: process XXXXX exited with exit code 1 and WaitStatus 256
```

**Root cause:** On **Intel Mac running macOS 15.4**, Docker Desktop's HyperKit hypervisor has a hardware acceleration conflict. HyperKit relies on VT-x hardware virtualization, which can be blocked by macOS security settings or conflicts with other installed hypervisors.

> **Important:** Apple's Virtualization Framework (`useVirtualizationFramework`) is **Apple Silicon only** and does NOT apply to Intel Macs. Enabling it on Intel causes Docker Desktop to silently fall back to HyperKit with no benefit.

**Solution:** Disabled hardware acceleration in Docker Desktop settings, forcing HyperKit to use software emulation:

```python
# ~/Library/Group Containers/group.com.docker/settings.json
{
  "disableHardwareAcceleration": true,
  "useVirtualizationFramework": false,
  "useVirtualizationFrameworkVirtioFS": false
}
```

After clearing the stale VM state and relaunching Docker Desktop, HyperKit started successfully and has remained stable.

---

### Issue 3 — Colima incompatible with Intel Mac + macOS 15.4

**Symptom:** `colima start` failed with:
```
vmType vz: On Intel Mac, macOS 15.5 or later is required to run Linux 6.12 or later.
```

**Root cause:** The Colima VM was configured with `vmType: vz` (Apple's Virtualization Framework via lima). On Intel Mac, `vz` requires macOS 15.5+. The installed macOS was 15.4.

**Solution attempted:** `colima start --vm-type qemu` would have worked (QEMU does not have the macOS version restriction). However, per project decision, Colima was uninstalled and Docker Desktop was used instead.

**Cleanup performed:**
```bash
colima delete --force
brew uninstall colima   # also removed lima dependency (50MB freed)
rm -rf ~/.colima
```

---

### Issue 4 — Docker file sharing (volume mount denied)

**Symptom:** Even after Docker Desktop started, `docker compose up` failed:
```
Error response from daemon: mounts denied:
The path .../localstack/init-aws.sh is not shared from the host
```

**Root cause:** Docker Desktop on Mac requires host paths to be explicitly listed in its file sharing configuration. The project directory (`/Users/sandeshlamsal/Documents/GitHub/...`) was not in the allowed list. The settings.json was updated to include `/Users/sandeshlamsal/Documents`, but Docker Desktop's VirtioFS daemon did not pick up the change on restart, likely because the VM state was also reset during troubleshooting.

**Solution:** Eliminated all host-path volume mounts from `docker-compose.yml` by baking files into the images at build time:

| What was mounted | What replaced it |
|---|---|
| `./localstack/init-aws.sh` volume mount | Custom `localstack/Dockerfile` that `COPY`s the init script into the image |
| `./config.yaml` volume mount for emitter | Build context changed to project root; `COPY config.yaml /config.yaml` in `emitter/Dockerfile` |
| `./config.yaml` volume mount for agent | Build context changed to project root; `COPY config.yaml /config.yaml` in `agent/Dockerfile` |

This is a more portable approach — the stack now has **zero host-path volume mounts**, making it reproducible on any machine regardless of Docker file sharing settings.

**docker-compose.yml change:**
```yaml
# Before (broke on Docker Desktop for Mac)
emitter:
  build: ./emitter
  volumes:
    - "./config.yaml:/config.yaml:ro"

# After (portable, no volume mounts)
emitter:
  build:
    context: .
    dockerfile: emitter/Dockerfile
```

**Dockerfile change:**
```dockerfile
# Before
COPY . .

# After (project root as build context)
COPY emitter/ .
COPY config.yaml /config.yaml
```

---

## Docker Desktop Notes (Intel Mac)

If you are running **Intel Mac** (not Apple Silicon):

1. **Do not enable Apple Virtualization Framework** — it is Apple Silicon only and will not help
2. If HyperKit crashes on startup, apply this fix:
   - Quit Docker Desktop fully
   - Edit `~/Library/Group Containers/group.com.docker/settings.json`
   - Set `"disableHardwareAcceleration": true`
   - Delete `~/Library/Containers/com.docker.docker/Data/vms/0` (stale VM state)
   - Relaunch Docker Desktop
3. If using **Colima** as an alternative, always start with `--vm-type qemu` on Intel + macOS < 15.5:
   ```bash
   colima start --vm-type qemu
   ```

---

## Testing & Verification

This section shows exactly what to run and what to look for to confirm every layer of the pipeline is working.

---

### Step 1 — Start the Stack

```bash
# With mock Claude (no API key needed — great for testing)
CLAUDE_MOCK=true docker compose up --build

# With real Claude API
docker compose up --build
```

Open **4 terminal tabs** — one per container — to watch each layer independently.

---

### Step 2 — Verify Each Container

#### Terminal 1 — Emitter (log generator)

```bash
docker compose logs -f emitter
```

**What you should see** — steady log lines every 2s, and an anomaly burst every 60s:

```
2026-04-19 17:25:11 [emitter] Starting emitter → /microservice/payment-service / application  interval=2.0s
2026-04-19 17:26:11 [emitter] Injected anomaly burst: cascading_timeout (8 events)
2026-04-19 17:27:11 [emitter] Injected anomaly burst: auth_failure_storm (8 events)
2026-04-19 17:28:12 [emitter] Injected anomaly burst: latency_spike (8 events)
```

Three burst scenarios cycle in rotation every 60 seconds:
- `cascading_timeout` — 8 × DB connection timeout errors
- `auth_failure_storm` — 8 × 401 authentication failures
- `latency_spike` — 8 × extreme latency warnings (5000-9000ms)

---

#### Terminal 2 — Agent (analysis loop)

```bash
docker compose logs -f agent
```

**What you should see** — buffering logs, sending to Claude, publishing findings:

```
2026-04-19 17:26:21 [agent] WARNING  CLAUDE_MOCK=true — using mock responses, no real API calls will be made
2026-04-19 17:26:21 [agent] INFO     Agent started — polling /microservice/payment-service/application every 15.0s, batch=20, min_severity=medium
2026-04-19 17:26:21 [agent] INFO     SNS topic: arn:aws:sns:us-east-1:000000000000:anomaly-findings

2026-04-19 17:26:21 [agent] INFO     Buffered 14 new log lines (total buffered: 24)
2026-04-19 17:26:21 [agent] INFO     Analyzing batch of 20 lines...
2026-04-19 17:26:21 [agent] INFO     [MOCK] Returning mock finding: anomaly_detected=True type=cascading_timeout
2026-04-19 17:26:21 [agent] WARNING  Anomaly detected — type=cascading_timeout severity=high service=payment-service
2026-04-19 17:26:21 [agent] WARNING  Root cause: [MOCK] Multiple consecutive DB timeouts...
2026-04-19 17:26:21 [agent] INFO     Published to SNS — MessageId: b6205f77-251c-4554-b88d-e26e7a682577
2026-04-19 17:26:21 [agent] INFO     Wrote finding directly to SQS watcher queue

2026-04-19 17:26:36 [agent] INFO     Buffered 8 new log lines (total buffered: 8)
2026-04-19 17:26:51 [agent] INFO     No anomaly detected in batch.
```

Key things to verify:
- `Buffered N new log lines` — agent is reading from CloudWatch
- `Analyzing batch of 20 lines` — batch threshold reached, calling Claude
- `Anomaly detected` — Claude (or mock) returned a finding
- `Published to SNS — MessageId: ...` — finding sent to SNS
- `Wrote finding directly to SQS watcher queue` — watcher will pick this up

---

#### Terminal 3 — Watcher (live findings display)

```bash
docker compose logs -f watcher
```

**What you should see** — color-coded anomaly cards printed as they arrive:

```
────────────────────────────────────────────────────────────
  ANOMALY DETECTED
────────────────────────────────────────────────────────────
  Type:     cascading_timeout
  Severity: HIGH                          ← red
  Service:  payment-service

  Root Cause:
    [MOCK] Multiple consecutive database connection timeouts
    detected (latency 4800-5500ms). The pattern suggests the
    upstream DB connection pool is exhausted.

  Action:
    Check DB connection pool limits. Scale pool size or shed
    load until DB recovers.
────────────────────────────────────────────────────────────


────────────────────────────────────────────────────────────
  ANOMALY DETECTED
────────────────────────────────────────────────────────────
  Type:     auth_failure_storm
  Severity: HIGH                          ← red
  Service:  auth-service

  Root Cause:
    [MOCK] Burst of 401 authentication failures across
    multiple request IDs within a 30-second window.

  Action:
    Rotate and re-deploy credentials. Check key expiry
    in secrets manager.
────────────────────────────────────────────────────────────
```

**Severity color key:**

| Severity | Color | Meaning |
|---|---|---|
| `low` | Blue | Isolated, self-resolving |
| `medium` | Yellow | Degraded but functional |
| `high` | Red | Significant user impact |
| `critical` | Magenta | Service down |

---

#### Terminal 4 — LocalStack (AWS mock health)

```bash
docker compose logs -f localstack
```

**What you should see** — AWS API calls being served locally:

```
LocalStack version: 3.4.0
Ready.
==> Creating CloudWatch log group: /microservice/payment-service
==> Creating CloudWatch log stream: application
==> Creating SNS topic: anomaly-findings
    SNS Topic ARN: arn:aws:sns:us-east-1:000000000000:anomaly-findings
==> Creating SQS queue: anomaly-findings-watcher
    SQS Queue URL: http://...
==> LocalStack init complete.

AWS logs.CreateLogGroup   => 200
AWS logs.CreateLogStream  => 200
AWS logs.PutLogEvents     => 200    ← emitter writing logs
AWS logs.GetLogEvents     => 200    ← agent reading logs
AWS sns.Publish           => 200    ← agent publishing findings
AWS sqs.SendMessage       => 200    ← direct SQS write
AWS sqs.ReceiveMessage    => 200    ← watcher polling
```

---

### Step 3 — Spot-Check via AWS CLI

LocalStack exposes a real AWS CLI-compatible API on `localhost:4566`. Use it to inspect state without restarting anything.

```bash
# See recent logs written by the emitter
aws --endpoint-url=http://localhost:4566 --region us-east-1 \
  logs get-log-events \
  --log-group-name /microservice/payment-service \
  --log-stream-name application \
  --limit 5

# Confirm SNS topic exists
aws --endpoint-url=http://localhost:4566 --region us-east-1 \
  sns list-topics

# Confirm SQS queue exists and check message counts
aws --endpoint-url=http://localhost:4566 --region us-east-1 \
  sqs get-queue-attributes \
  --queue-url http://localhost:4566/000000000000/anomaly-findings-watcher \
  --attribute-names ApproximateNumberOfMessages

# Manually publish a test finding to SNS and watch it appear in watcher
aws --endpoint-url=http://localhost:4566 --region us-east-1 \
  sns publish \
  --topic-arn arn:aws:sns:us-east-1:000000000000:anomaly-findings \
  --message '{"anomaly_detected":true,"severity":"critical","anomaly_type":"error_rate_spike","affected_service":"manual-test","root_cause_summary":"Manual test publish.","recommended_action":"No action needed."}'
```

---

### Step 4 — Full Pipeline Flow Diagram

```
Every 2s:
  emitter ──► CloudWatch Logs (PutLogEvents ✓)

Every 15s:
  agent   ──► CloudWatch Logs (GetLogEvents ✓)
          ──► [batch 20 logs]
          ──► Claude API / Mock (analyze ✓)
          ──► SNS Publish (MessageId returned ✓)
          ──► SQS SendMessage (direct write ✓)

Continuously:
  watcher ──► SQS ReceiveMessage (long-poll 10s ✓)
          ──► prints color-coded finding to stdout ✓
```

**Every step has a visible log line confirming it happened.** If any step is silent, check that container's logs for errors.

---

### Quick Health Check (one command)

```bash
docker compose ps
```

All 4 containers must show `Up` or `Up (healthy)`:

```
NAME              STATUS
localstack-1      Up (healthy)    ← LocalStack AWS mock ready
emitter-1         Up              ← writing logs every 2s
agent-1           Up              ← polling + analyzing every 15s
watcher-1         Up              ← printing findings in real time
```

If any container shows `Exited`, check its logs:
```bash
docker compose logs <service-name>
```

---

## Local Development (without Docker)

```bash
# Install dependencies
pip install boto3 anthropic pyyaml

# Point boto3 at a running LocalStack
export AWS_DEFAULT_REGION=us-east-1
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export LOCALSTACK_ENDPOINT=http://localhost:4566
export ANTHROPIC_API_KEY=sk-ant-...

# Run emitter
python emitter/emitter.py

# Run agent (in a second terminal)
python agent/agent.py

# Run watcher (in a third terminal)
python watcher/watcher.py
```

---

## Cost Considerations

- The agent uses **prompt caching** (`cache_control: ephemeral`) on the system prompt — repeated calls within 5 minutes reuse the cached prefix, cutting input token cost by ~90% for the taxonomy portion
- With `LOG_BATCH_SIZE=20` and `POLL_INTERVAL_SEC=15`, expect roughly 4 Claude calls/minute during active anomaly periods
- Swap `claude-sonnet-4-6` → `claude-haiku-4-5-20251001` in `config.yaml` for lower cost during development
