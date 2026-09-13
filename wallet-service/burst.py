"""One-command burst script reproducing all 3 live probes.

Usage:
  python burst.py --url http://localhost:8000 [--n 50 --k 30 --m 200]
  BASE_URL env also supported.
"""
import argparse
import concurrent.futures as cf
import os
import sys
import uuid
import httpx

TIMEOUT = 60.0


def mint(base: str, user: str) -> dict:
    r = httpx.post(f"{base}/auth/token", json={"user_id": user}, timeout=TIMEOUT)
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.getenv("BASE_URL", "http://localhost:8000"))
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--k", type=int, default=30)
    ap.add_argument("--m", type=int, default=200)
    args = ap.parse_args()
    base = args.url.rstrip("/")
    assert httpx.get(f"{base}/health", timeout=TIMEOUT).status_code == 200, "health check failed"
    tag = uuid.uuid4().hex[:8]

    # Gate 1: concurrent get-or-create
    h = mint(base, f"burst-{tag}")
    fresh = f"burst-user-{tag}"
    with cf.ThreadPoolExecutor(max_workers=args.n) as ex:
        futs = [ex.submit(lambda: httpx.post(f"{base}/wallets", json={"user_id": fresh}, headers=h, timeout=TIMEOUT).json().get("id")) for _ in range(args.n)]
        ids = [f.result() for f in futs]
    uniq = set(ids)
    print(f"[gate1] N={args.n} distinct_wallets={len(uniq)} {'PASS' if len(uniq)==1 else 'FAIL'}")

    # Wallets for gates 2-3
    wa = httpx.post(f"{base}/wallets", json={"user_id": f"ba-{tag}"}, headers=h, timeout=TIMEOUT).json()
    wb = httpx.post(f"{base}/wallets", json={"user_id": f"bb-{tag}"}, headers=h, timeout=TIMEOUT).json()
    wc = httpx.post(f"{base}/wallets", json={"user_id": f"bc-{tag}"}, headers=h, timeout=TIMEOUT).json()
    for w in (wa, wb, wc):
        httpx.post(f"{base}/_test/fund", json={"wallet_id": w["id"], "amount_paise": 100000}, headers=h, timeout=TIMEOUT).raise_for_status()

    # Gate 2: idempotent retry storm
    key = f"storm-{tag}"
    body = {"from_wallet": wa["id"], "to_wallet": wb["id"], "amount_paise": 1000, "idempotency_key": key}
    ba0 = httpx.get(f"{base}/wallets/{wa['id']}", headers=h, timeout=TIMEOUT).json()["balance_paise"]
    bb0 = httpx.get(f"{base}/wallets/{wb['id']}", headers=h, timeout=TIMEOUT).json()["balance_paise"]
    def one(_):
        r = httpx.post(f"{base}/transfers", json=body, headers=h, timeout=TIMEOUT)
        return (r.status_code, r.json().get("id"))
    with cf.ThreadPoolExecutor(max_workers=args.k) as ex:
        res = list(ex.map(one, range(args.k)))
    tids = {t for _, t in res if t}
    ba1 = httpx.get(f"{base}/wallets/{wa['id']}", headers=h, timeout=TIMEOUT).json()["balance_paise"]
    bb1 = httpx.get(f"{base}/wallets/{wb['id']}", headers=h, timeout=TIMEOUT).json()["balance_paise"]
    ok2 = len(tids) == 1 and (ba0 - ba1 == 1000) and (bb1 - bb0 == 1000)
    print(f"[gate2] K={args.k} distinct_transfers={len(tids)} debit={ba0-ba1} credit={bb1-bb0} {'PASS' if ok2 else 'FAIL'}")

    # same-key-different-body must 409
    bad = dict(body); bad["amount_paise"] = 999
    r409 = httpx.post(f"{base}/transfers", json=bad, headers=h, timeout=TIMEOUT)
    print(f"[gate2b] reuse-key-different-body status={r409.status_code} {'PASS' if r409.status_code==409 else 'FAIL'}")

    # Gate 3: conservation under contention incl A<->B
    import random
    random.seed(7)
    wallets = [wa["id"], wb["id"], wc["id"]]
    def bal(w): return httpx.get(f"{base}/wallets/{w}", headers=h, timeout=TIMEOUT).json()["balance_paise"]
    total0 = sum(bal(w) for w in wallets)
    jobs = []
    for i in range(args.m):
        f, t = random.sample(wallets, 2)
        amt = random.choice([100, 500, 500000])
        jobs.append({"from_wallet": f, "to_wallet": t, "amount_paise": amt, "idempotency_key": f"g3-{tag}-{i}"})
    def job(b):
        return httpx.post(f"{base}/transfers", json=b, headers=h, timeout=TIMEOUT).status_code
    with cf.ThreadPoolExecutor(max_workers=32) as ex:
        codes = list(ex.map(job, jobs))
    bals = [bal(w) for w in wallets]
    ok3 = sum(bals) == total0 and all(b >= 0 for b in bals)
    print(f"[gate3] M={args.m} total {total0}->{sum(bals)} min_bal={min(bals)} codes={sorted(set(codes))} {'PASS' if ok3 else 'FAIL'}")

    # metrics sanity
    mm = httpx.get(f"{base}/metrics", timeout=TIMEOUT).text
    assert "transfers_created_total" in mm
    print("[metrics] PASS")
    if not (len(uniq) == 1 and ok2 and ok3 and r409.status_code == 409):
        print("BURST RESULT: FAIL"); sys.exit(1)
    print("BURST RESULT: PASS")


if __name__ == "__main__":
    main()
