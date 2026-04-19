import random
import uuid

SERVICES = ["payment-service", "auth-service", "order-service", "inventory-service"]

INFO_TEMPLATES = [
    lambda: {
        "level": "INFO",
        "message": "Request completed successfully",
        "status_code": 200,
        "latency_ms": random.randint(40, 300),
        "endpoint": random.choice(["/api/pay", "/api/order", "/api/verify"]),
    },
    lambda: {
        "level": "INFO",
        "message": "Cache hit for user session",
        "status_code": 200,
        "latency_ms": random.randint(5, 30),
        "endpoint": "/api/session",
    },
    lambda: {
        "level": "INFO",
        "message": "Health check passed",
        "status_code": 200,
        "latency_ms": random.randint(1, 10),
        "endpoint": "/health",
    },
]

WARNING_TEMPLATES = [
    lambda: {
        "level": "WARNING",
        "message": "High latency detected on upstream dependency",
        "status_code": 200,
        "latency_ms": random.randint(1200, 2500),
        "endpoint": "/api/pay",
        "retry_count": random.randint(1, 2),
    },
    lambda: {
        "level": "WARNING",
        "message": "Rate limit threshold approaching",
        "status_code": 429,
        "latency_ms": random.randint(50, 200),
        "endpoint": "/api/order",
    },
    lambda: {
        "level": "WARNING",
        "message": "Slow DB query exceeded 500ms threshold",
        "status_code": 200,
        "latency_ms": random.randint(500, 1200),
        "endpoint": "/api/inventory",
        "query": "SELECT * FROM orders WHERE ...",
    },
]

ERROR_TEMPLATES = [
    lambda: {
        "level": "ERROR",
        "message": "Database connection timeout after 3 retries",
        "status_code": 503,
        "latency_ms": random.randint(4500, 6000),
        "endpoint": "/api/pay",
        "retry_count": 3,
        "error": "ConnectionTimeout",
    },
    lambda: {
        "level": "ERROR",
        "message": "Upstream service returned 500",
        "status_code": 500,
        "latency_ms": random.randint(200, 800),
        "endpoint": "/api/order",
        "error": "InternalServerError",
    },
    lambda: {
        "level": "ERROR",
        "message": "Authentication token validation failed",
        "status_code": 401,
        "latency_ms": random.randint(30, 100),
        "endpoint": "/api/verify",
        "error": "InvalidToken",
    },
]

# Anomaly burst scenarios — emit a tight cluster of these to trigger detection
ANOMALY_SCENARIOS = {
    "cascading_timeout": [
        lambda: {
            "level": "ERROR",
            "message": "Database connection timeout after 3 retries",
            "status_code": 503,
            "latency_ms": random.randint(4800, 5500),
            "endpoint": "/api/pay",
            "retry_count": 3,
            "error": "ConnectionTimeout",
        }
    ] * 8,
    "auth_failure_storm": [
        lambda: {
            "level": "ERROR",
            "message": "Authentication token validation failed",
            "status_code": 401,
            "latency_ms": random.randint(20, 80),
            "endpoint": "/api/verify",
            "error": "InvalidToken",
        }
    ] * 8,
    "latency_spike": [
        lambda: {
            "level": "WARNING",
            "message": "Extreme latency spike detected",
            "status_code": 200,
            "latency_ms": random.randint(5000, 9000),
            "endpoint": random.choice(["/api/pay", "/api/order"]),
            "retry_count": 0,
        }
    ] * 8,
}


def make_log(level: str) -> dict:
    if level == "INFO":
        template = random.choice(INFO_TEMPLATES)
    elif level == "WARNING":
        template = random.choice(WARNING_TEMPLATES)
    else:
        template = random.choice(ERROR_TEMPLATES)
    base = template()
    base["service"] = random.choice(SERVICES)
    base["request_id"] = str(uuid.uuid4())[:8]
    return base


def make_anomaly_burst(scenario: str) -> list[dict]:
    templates = ANOMALY_SCENARIOS.get(scenario, ANOMALY_SCENARIOS["cascading_timeout"])
    return [
        {**t(), "service": "payment-service", "request_id": str(uuid.uuid4())[:8]}
        for t in templates
    ]
