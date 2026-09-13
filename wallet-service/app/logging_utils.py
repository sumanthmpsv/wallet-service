import json
import sys
import uuid
from fastapi import Request


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:16]


def log_event(event: str, correlation_id: str, **fields):
    rec = {"event": event, "correlation_id": correlation_id, **fields}
    sys.stdout.write(json.dumps(rec) + "\n")
    sys.stdout.flush()
