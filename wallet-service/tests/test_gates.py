"""Live-probe gates: concurrency invariants (TDD)."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient

from app.main import app as fastapp


def _fresh_clients(n):
    # Each thread gets its own TestClient to avoid portal contention.
    return [TestClient(fastapp) for _ in range(n)]


def _auth_header(user="probe"):
    from app.auth import create_access_token
    return {"Authorization": f"Bearer {create_access_token(user)}"}


def test_gate1_concurrent_get_or_create(client, token_for):
    h = token_for("gate1")
    fresh_user = f"race-{uuid.uuid4().hex[:10]}"
    N = 20  # keep CI fast; burst.py does 50 against live URL

    def one(_):
        from fastapi.testclient import TestClient as TC
        with TC(fastapp) as c:
            r = c.post("/wallets", json={"user_id": fresh_user}, headers=h)
            return r.json().get("id") if r.status_code == 200 else f"ERR:{r.text}"

    with ThreadPoolExecutor(max_workers=N) as ex:
        ids = list(ex.map(one, range(N)))
    assert all(not str(i).startswith("ERR") for i in ids), ids
    assert len(set(ids)) == 1, f"race created multiple wallets: {set(ids)}"


def test_gate2_idempotent_retry_storm(client, token_for):
    from tests.conftest import make_wallet, fund, get_balance
    h = token_for("gate2")
    a = make_wallet(client, h, f"g2a-{uuid.uuid4().hex[:6]}")
    b = make_wallet(client, h, f"g2b-{uuid.uuid4().hex[:6]}")
    fund(client, h, a["id"], 10000)
    fund(client, h, b["id"], 10000)
    bal_a0 = get_balance(client, h, a["id"])
    bal_b0 = get_balance(client, h, b["id"])
    key = f"storm-{uuid.uuid4()}"
    body = {"from_wallet": a["id"], "to_wallet": b["id"], "amount_paise": 1000, "idempotency_key": key}
    K = 20

    def one(_):
        from fastapi.testclient import TestClient as TC
        with TC(fastapp) as c:
            r = c.post("/transfers", json=body, headers=h)
            j = r.json() if r.status_code in (200, 201) else {}
            return (r.status_code, j.get("id"))

    with ThreadPoolExecutor(max_workers=K) as ex:
        res = list(ex.map(one, range(K)))
    codes = {c for c, _ in res}
    assert codes <= {200, 201}, f"storm must have zero 500s, got codes: {sorted(codes)}"
    ids = {tid for _, tid in res if tid}
    assert len(ids) == 1, f"double-apply under storm: {res}"
    # exactly one debit/credit
    assert get_balance(client, h, a["id"]) == bal_a0 - 1000
    assert get_balance(client, h, b["id"]) == bal_b0 + 1000


def test_gate3_conservation_under_contention(client, token_for):
    from tests.conftest import make_wallet, fund, get_balance
    h = token_for("gate3")
    suffix = uuid.uuid4().hex[:6]
    wallets = [make_wallet(client, h, f"g3-{suffix}-{i}")["id"] for i in range(3)]
    for w in wallets:
        fund(client, h, w, 10000)
    total0 = sum(get_balance(client, h, w) for w in wallets)

    import random
    random.seed(7)
    jobs = []
    for i in range(60):
        f, t = random.sample(wallets, 2)
        amt = random.choice([100, 500, 20000])  # 20000 forces overdraft declines
        jobs.append((f, t, amt, f"g3-{suffix}-{i}"))

    def one(job):
        f, t, amt, key = job
        from fastapi.testclient import TestClient as TC
        with TC(fastapp) as c:
            r = c.post("/transfers", json={
                "from_wallet": f, "to_wallet": t,
                "amount_paise": amt, "idempotency_key": key}, headers=h)
            return r.status_code

    with ThreadPoolExecutor(max_workers=16) as ex:
        codes = list(ex.map(one, jobs))
    assert any(c in (201, 200) for c in codes)
    assert any(c == 402 for c in codes), f"expected some declines, got {set(codes)}"
    bals = [get_balance(client, h, w) for w in wallets]
    assert all(b >= 0 for b in bals), bals
    assert sum(bals) == total0, f"conservation broken: {total0} -> {sum(bals)}"


def test_gate3_opposite_direction_no_deadlock(client, token_for):
    """A->B and B->A at once must not deadlock (sorted lock order)."""
    from tests.conftest import make_wallet, fund, get_balance
    h = token_for("gate3ab")
    a = make_wallet(client, h, f"ab-a-{uuid.uuid4().hex[:6]}")["id"]
    b = make_wallet(client, h, f"ab-b-{uuid.uuid4().hex[:6]}")["id"]
    fund(client, h, a, 10000)
    fund(client, h, b, 10000)
    total0 = get_balance(client, h, a) + get_balance(client, h, b)

    def ab(i):
        from fastapi.testclient import TestClient as TC
        with TC(fastapp) as c:
            return c.post("/transfers", json={
                "from_wallet": a, "to_wallet": b, "amount_paise": 100,
                "idempotency_key": f"ab-{uuid.uuid4()}"}, headers=h).status_code

    def ba(i):
        from fastapi.testclient import TestClient as TC
        with TC(fastapp) as c:
            return c.post("/transfers", json={
                "from_wallet": b, "to_wallet": a, "amount_paise": 100,
                "idempotency_key": f"ba-{uuid.uuid4()}"}, headers=h).status_code

    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = [ex.submit(ab, i) for i in range(15)] + [ex.submit(ba, i) for i in range(15)]
        codes = [f.result(timeout=30) for f in futs]
    assert all(c in (200, 201) for c in codes), codes
    assert get_balance(client, h, a) + get_balance(client, h, b) == total0
