import os
import pytest
from fastapi.testclient import TestClient

# Ensure test DB URL set before app import
os.environ.setdefault("DATABASE_URL", os.getenv("DATABASE_URL", "postgresql://wallet:wallet@localhost:5432/wallet"))
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("ALLOW_TEST_FUND", "1")

from app.main import app  # noqa: E402
from app.db import init_db, get_conn  # noqa: E402


@pytest.fixture(scope="session")
def client():
    init_db()
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def token_for():
    # returns function user_id -> headers
    from app.auth import create_access_token
    def _h(user: str = "tester"):
        tok = create_access_token(user)
        return {"Authorization": f"Bearer {tok}"}
    return _h


@pytest.fixture(autouse=True)
def clean_tables():
    yield
    # clean between tests to keep idempotency keys unique per test (use uuids anyway).
    # We TRUNCATE transfers but keep wallets? Keep wallets to avoid churn; only clean transfers
    # older than test? Simplest: delete nothing — tests use uuid keys so no clash.
    pass


def make_wallet(c: TestClient, headers: dict, user_id: str) -> dict:
    r = c.post("/wallets", json={"user_id": user_id}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def fund(c: TestClient, headers: dict, wallet_id: str, amount: int) -> dict:
    r = c.post("/_test/fund", json={"wallet_id": wallet_id, "amount_paise": amount}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def get_balance(c: TestClient, headers: dict, wallet_id: str) -> int:
    r = c.get(f"/wallets/{wallet_id}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["balance_paise"]
