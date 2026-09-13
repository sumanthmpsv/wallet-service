"""Contract + validation tests (TDD red phase first)."""
import uuid


def test_auth_required(client):
    r = client.post("/wallets", json={"user_id": "nouser"})
    assert r.status_code in (401, 403)


def test_invalid_jwt_rejected(client):
    r = client.post("/wallets", json={"user_id": "x"},
                    headers={"Authorization": "Bearer bogus"})
    assert r.status_code == 401


def test_mint_and_use_jwt(client):
    r = client.post("/auth/token", json={"user_id": "alice"})
    assert r.status_code == 200
    tok = r.json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    w = client.post("/wallets", json={"user_id": f"alice-{uuid.uuid4().hex[:8]}"}, headers=h)
    assert w.status_code == 200
    assert "balance_paise" in w.json()


def test_create_and_get_wallet(client, token_for):
    h = token_for("u1")
    uid = f"u1-{uuid.uuid4().hex[:8]}"
    r = client.post("/wallets", json={"user_id": uid}, headers=h)
    assert r.status_code == 200
    wid = r.json()["id"]
    g = client.get(f"/wallets/{wid}", headers=h)
    assert g.status_code == 200
    assert g.json()["balance_paise"] >= 0


def test_get_missing_wallet_404(client, token_for):
    h = token_for("u1")
    r = client.get(f"/wallets/{uuid.uuid4()}", headers=h)
    assert r.status_code == 404


def test_float_amount_rejected(client, token_for):
    from tests.conftest import make_wallet, fund
    h = token_for("u-float")
    a = make_wallet(client, h, f"fa-{uuid.uuid4().hex[:6]}")
    b = make_wallet(client, h, f"fb-{uuid.uuid4().hex[:6]}")
    fund(client, h, a["id"], 10000)
    # float rupees must be rejected with 422
    r = client.post("/transfers", json={
        "from_wallet": a["id"], "to_wallet": b["id"],
        "amount_paise": 10.5, "idempotency_key": f"k-{uuid.uuid4()}"}, headers=h)
    assert r.status_code == 422, r.text
    # decimal string rejected
    r2 = client.post("/transfers", json={
        "from_wallet": a["id"], "to_wallet": b["id"],
        "amount_paise": "10.5", "idempotency_key": f"k-{uuid.uuid4()}"}, headers=h)
    assert r2.status_code == 422, r2.text


def test_zero_negative_rejected(client, token_for):
    from tests.conftest import make_wallet
    h = token_for("u-neg")
    a = make_wallet(client, h, f"na-{uuid.uuid4().hex[:6]}")
    b = make_wallet(client, h, f"nb-{uuid.uuid4().hex[:6]}")
    for bad in [0, -100]:
        r = client.post("/transfers", json={
            "from_wallet": a["id"], "to_wallet": b["id"],
            "amount_paise": bad, "idempotency_key": f"k-{uuid.uuid4()}"}, headers=h)
        assert r.status_code == 422, (bad, r.text)


def test_self_transfer_400(client, token_for):
    from tests.conftest import make_wallet, fund
    h = token_for("u-self")
    a = make_wallet(client, h, f"s-{uuid.uuid4().hex[:6]}")
    fund(client, h, a["id"], 1000)
    r = client.post("/transfers", json={
        "from_wallet": a["id"], "to_wallet": a["id"],
        "amount_paise": 100, "idempotency_key": f"k-{uuid.uuid4()}"}, headers=h)
    assert r.status_code == 400


def test_missing_wallet_404(client, token_for):
    from tests.conftest import make_wallet
    h = token_for("u-miss")
    a = make_wallet(client, h, f"m-{uuid.uuid4().hex[:6]}")
    r = client.post("/transfers", json={
        "from_wallet": a["id"], "to_wallet": str(uuid.uuid4()),
        "amount_paise": 100, "idempotency_key": f"k-{uuid.uuid4()}"}, headers=h)
    assert r.status_code == 404


def test_overdraft_declined_cleanly(client, token_for):
    from tests.conftest import make_wallet, fund, get_balance
    h = token_for("u-od")
    a = make_wallet(client, h, f"oda-{uuid.uuid4().hex[:6]}")
    b = make_wallet(client, h, f"odb-{uuid.uuid4().hex[:6]}")
    fund(client, h, a["id"], 500)
    before_b = get_balance(client, h, b["id"])
    r = client.post("/transfers", json={
        "from_wallet": a["id"], "to_wallet": b["id"],
        "amount_paise": 5000, "idempotency_key": f"k-{uuid.uuid4()}"}, headers=h)
    assert r.status_code == 402, r.text
    assert r.json()["status"] == "declined"
    # no partial apply
    assert get_balance(client, h, a["id"]) == 500
    assert get_balance(client, h, b["id"]) == before_b


def test_idempotent_retry_same_body_returns_same_id(client, token_for):
    from tests.conftest import make_wallet, fund
    h = token_for("u-idem")
    a = make_wallet(client, h, f"ia-{uuid.uuid4().hex[:6]}")
    b = make_wallet(client, h, f"ib-{uuid.uuid4().hex[:6]}")
    fund(client, h, a["id"], 5000)
    key = f"k-{uuid.uuid4()}"
    body = {"from_wallet": a["id"], "to_wallet": b["id"], "amount_paise": 1000, "idempotency_key": key}
    r1 = client.post("/transfers", json=body, headers=h)
    assert r1.status_code == 201, r1.text
    r2 = client.post("/transfers", json=body, headers=h)
    assert r2.status_code == 200, r2.text
    assert r1.json()["id"] == r2.json()["id"]


def test_same_key_different_body_409(client, token_for):
    from tests.conftest import make_wallet, fund
    h = token_for("u-conf")
    a = make_wallet(client, h, f"ca-{uuid.uuid4().hex[:6]}")
    b = make_wallet(client, h, f"cb-{uuid.uuid4().hex[:6]}")
    fund(client, h, a["id"], 5000)
    key = f"k-{uuid.uuid4()}"
    r1 = client.post("/transfers", json={
        "from_wallet": a["id"], "to_wallet": b["id"],
        "amount_paise": 100, "idempotency_key": key}, headers=h)
    assert r1.status_code == 201
    r2 = client.post("/transfers", json={
        "from_wallet": a["id"], "to_wallet": b["id"],
        "amount_paise": 200, "idempotency_key": key}, headers=h)
    assert r2.status_code == 409, r2.text


def test_get_transfer_by_id(client, token_for):
    from tests.conftest import make_wallet, fund
    h = token_for("u-get")
    a = make_wallet(client, h, f"ga-{uuid.uuid4().hex[:6]}")
    b = make_wallet(client, h, f"gb-{uuid.uuid4().hex[:6]}")
    fund(client, h, a["id"], 2000)
    r = client.post("/transfers", json={
        "from_wallet": a["id"], "to_wallet": b["id"],
        "amount_paise": 500, "idempotency_key": f"k-{uuid.uuid4()}"}, headers=h)
    tid = r.json()["id"]
    g = client.get(f"/transfers/{tid}", headers=h)
    assert g.status_code == 200
    assert g.json()["id"] == tid


def test_health_and_metrics(client, token_for):
    assert client.get("/health").status_code == 200
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "transfers_created_total" in r.text
    rj = client.get("/metrics.json")
    assert "latency_p99_ms" in rj.json()
