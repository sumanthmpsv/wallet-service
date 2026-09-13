"""In-memory metrics: request rate, p99 latency, error rate + domain counters."""
import threading
import time

_lock = threading.Lock()
request_count = 0
error_count = 0
latencies: list[float] = []
transfers_created = 0
transfers_declined = 0
idempotent_replays = 0
start_time = time.time()


def record_request(latency_s: float, is_error: bool):
    global request_count, error_count
    with _lock:
        request_count += 1
        if is_error:
            error_count += 1
        latencies.append(latency_s)
        if len(latencies) > 10000:
            del latencies[:5000]


def inc_domain(name: str):
    global transfers_created, transfers_declined, idempotent_replays
    with _lock:
        if name == "created":
            transfers_created += 1
        elif name == "declined":
            transfers_declined += 1
        elif name == "replay":
            idempotent_replays += 1


def percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    k = min(len(s) - 1, int(len(s) * p / 100))
    return s[k]


def snapshot():
    with _lock:
        uptime = time.time() - start_time
        return {
            "uptime_s": round(uptime, 1),
            "request_count": request_count,
            "error_count": error_count,
            "error_rate": (error_count / request_count) if request_count else 0.0,
            "latency_p50_ms": round(percentile(latencies, 50) * 1000, 2),
            "latency_p99_ms": round(percentile(latencies, 99) * 1000, 2),
            "transfers_created": transfers_created,
            "transfers_declined_insufficient_funds": transfers_declined,
            "idempotent_replays": idempotent_replays,
        }


def prometheus_text() -> str:
    s = snapshot()
    lines = [
        "# HELP http_requests_total total requests",
        "# TYPE http_requests_total counter",
        f"http_requests_total {s['request_count']}",
        "# HELP http_errors_total total error requests",
        "# TYPE http_errors_total counter",
        f"http_errors_total {s['error_count']}",
        "# HELP http_latency_p99_ms p99 latency ms",
        "# TYPE http_latency_p99_ms gauge",
        f"http_latency_p99_ms {s['latency_p99_ms']}",
        "# HELP transfers_created_total domain counter",
        "# TYPE transfers_created_total counter",
        f"transfers_created_total {s['transfers_created']}",
        "# HELP transfers_declined_total domain counter",
        "# TYPE transfers_declined_total counter",
        f"transfers_declined_total {s['transfers_declined_insufficient_funds']}",
        "# HELP idempotent_replays_total domain counter",
        "# TYPE idempotent_replays_total counter",
        f"idempotent_replays_total {s['idempotent_replays']}",
    ]
    return "\n".join(lines) + "\n"
