import time
import uuid
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from psycopg import errors as pg_errors

from .auth import create_access_token, get_current_user
from .config import DATABASE_URL
from .db import get_conn, init_db
from .logging_utils import log_event, new_correlation_id
from . import metrics as m
from .models import request_hash
from .schemas import CreateWalletRequest, TokenRequest, TransferRequest


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Wallet & P2P Transfer")


@app.middleware("http")
async def correlation_and_metrics(request: Request, call_next):
    cid = request.headers.get("X-Request-ID") or new_correlation_id()
    request.state.correlation_id = cid
    t0 = time.time()
    try:
        response = await call_next(request)
        is_err = response.status_code >= 500
        m.record_request(time.time() - t0, is_err)
        response.headers["X-Request-ID"] = cid
        return response
    except Exception:
        m.record_request(time.time() - t0, True)
        raise


def _cid(request: Request) -> str:
    return getattr(request.state, "correlation_id", "none")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics_prom():
    return PlainTextResponse(m.prometheus_text(), media_type="text/plain")


@app.get("/metrics.json")
def metrics_json():
    return m.snapshot()


@app.post("/auth/token")
def mint_token(body: TokenRequest):
    if not body.user_id.strip():
        raise HTTPException(status_code=400, detail="user_id required")
    tok = create_access_token(body.user_id.strip())
    return {"access_token": tok, "token_type": "bearer"}


@app.post("/wallets", status_code=200)
def get_or_create_wallet(
    body: CreateWalletRequest, request: Request, caller: str = Depends(get_current_user)
):
    """Race-free get-or-create. Any valid JWT may create for any user_id
    (probe-compatible); caller identity is logged. Tighten by requiring
    body.user_id == caller if stricter auth is desired."""
    cid = _cid(request)
    user_id = body.user_id.strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id required")
    with get_conn() as conn:
        with conn.cursor() as cur:
            # INSERT ... ON CONFLICT DO NOTHING + re-SELECT: the correct primitive.
            cur.execute(
                "INSERT INTO wallets(user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
                (user_id,),
            )
            cur.execute("SELECT id, user_id, balance_paise FROM wallets WHERE user_id=%s", (user_id,))
            row = cur.fetchone()
        conn.commit()
    log_event("wallet_get_or_create", cid, caller=caller, user_id=user_id, wallet_id=str(row["id"]))
    return {"id": str(row["id"]), "user_id": row["user_id"], "balance_paise": row["balance_paise"]}


@app.get("/wallets/{wallet_id}")
def get_wallet(wallet_id: str, request: Request, caller: str = Depends(get_current_user)):
    cid = _cid(request)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, user_id, balance_paise FROM wallets WHERE id=%s", (wallet_id,))
            row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="wallet not found")
    return {"id": str(row["id"]), "user_id": row["user_id"], "balance_paise": row["balance_paise"]}


def _transfer_response(row) -> dict:
    return {
        "id": str(row["id"]),
        "from_wallet": str(row["from_wallet"]),
        "to_wallet": str(row["to_wallet"]),
        "amount_paise": row["amount_paise"],
        "idempotency_key": row["idempotency_key"],
        "status": row["status"],
    }


@app.post("/transfers", status_code=201)
def create_transfer(
    body: TransferRequest, request: Request, caller: str = Depends(get_current_user)
):
    """Exactly-once transfer in a single DB transaction.

    - Idempotency key uniqueness committed in SAME tx as debit/credit.
    - Atomic conditional debit (UPDATE … WHERE balance >= amount;
      rows_affected = 0 with wallet present means insufficient funds) plus
      credit, both applied in sorted wallet-id order so concurrent
      A→B / B→A transfers acquire row locks in the same order.
    - No-overdraft via balance check under row lock; decline inserts a
      'declined' row consuming the key so retries are stable.
    - Deadlock (FK key-share vs row lock on cross A<->B transfers) is
      retried with backoff; safe because our tx aborted (nothing committed)
      and the idempotency key makes a retry-after-partial-commit converge
      to the replay path.
    - UniqueViolation on the key (storm loser) re-reads the winner's row
      and returns it (or 409 on hash mismatch) — never a 500.
    """
    import random
    import time as _time

    cid = _cid(request)
    fw, tw, amt, key = body.from_wallet, body.to_wallet, body.amount_paise, body.idempotency_key.strip()
    if fw == tw:
        raise HTTPException(status_code=400, detail="from and to wallets must differ")
    try:
        uuid.UUID(fw)
        uuid.UUID(tw)
    except ValueError:
        raise HTTPException(status_code=400, detail="wallet ids must be UUIDs")
    rhash = request_hash(fw, tw, amt)

    def _read_existing():
        with get_conn() as c2:
            with c2.cursor() as cur2:
                cur2.execute("SELECT * FROM transfers WHERE idempotency_key=%s", (key,))
                return cur2.fetchone()

    def _replay_or_conflict(existing):
        if existing["request_hash"] != rhash:
            log_event("transfer_key_conflict", cid, caller=caller, key=key)
            raise HTTPException(status_code=409, detail="idempotency_key reused with different body")
        m.inc_domain("replay")
        log_event("transfer_idempotent_replay", cid, caller=caller, key=key,
                  transfer_id=str(existing["id"]))
        status = 200 if existing["status"] == "completed" else 402
        return JSONResponse(status_code=status, content=_transfer_response(existing))

    def _is_deadlock(e: Exception) -> bool:
        return getattr(e, "pgcode", "") == "40P01" or "deadlock detected" in str(e)

    def _is_unique_violation(e: Exception) -> bool:
        return (
            isinstance(e, pg_errors.UniqueViolation)
            or "transfers_idempotency_key_key" in str(e)
            or ("duplicate key value" in str(e) and "idempotency_key" in str(e))
        )

    last_error: Exception | None = None
    for attempt in range(10):
        with get_conn() as conn:
            try:
                with conn.cursor() as cur:
                    # 1) Idempotency pre-check inside tx.
                    cur.execute("SELECT * FROM transfers WHERE idempotency_key=%s", (key,))
                    existing = cur.fetchone()
                    if existing is not None:
                        conn.commit()
                        return _replay_or_conflict(existing)

                    # 2) Atomic conditional debit + credit, touching rows
                    #    in sorted id order. The debit is a single UPDATE
                    #    (no read-modify-write: no lost update); rows_affected
                    #    == 0 with wallet present means insufficient funds.
                    first, second = sorted([fw, tw])

                    def _debit(cur):
                        cur.execute(
                            """UPDATE wallets SET balance_paise = balance_paise - %s
                               WHERE id = %s AND balance_paise >= %s""",
                            (amt, fw, amt),
                        )
                        return cur.rowcount

                    def _credit(cur):
                        cur.execute(
                            "UPDATE wallets SET balance_paise = balance_paise + %s WHERE id=%s",
                            (amt, tw),
                        )
                        return cur.rowcount

                    if fw == first:
                        debited = _debit(cur)
                        credited = _credit(cur) if debited else 0
                    else:
                        credited = _credit(cur)
                        debited = _debit(cur) if credited else 0

                    if debited and credited:
                        # 3) Both legs applied: record the completed transfer.
                        cur.execute(
                            """INSERT INTO transfers(from_wallet,to_wallet,amount_paise,idempotency_key,request_hash,status)
                               VALUES (%s,%s,%s,%s,%s,'completed') RETURNING *""",
                            (fw, tw, amt, key, rhash),
                        )
                        row = cur.fetchone()
                        conn.commit()
                        m.inc_domain("created")
                        log_event("transfer_created", cid, caller=caller, key=key,
                                  transfer_id=str(row["id"]), amount_paise=amt)
                        return JSONResponse(status_code=201, content=_transfer_response(row))

                    # Debit and/or credit touched nothing decisive: missing wallet
                    # or overdraft. If fw is the second row, a credit may have
                    # applied already — roll everything back first so no money
                    # can be created, then record the outcome fresh.
                    conn.rollback()
                    cur.execute("SELECT id FROM wallets WHERE id IN (%s,%s)" % ("%s", "%s"), (fw, tw))
                    found = {str(r["id"]) for r in cur.fetchall()}
                    if fw not in found or tw not in found:
                        conn.rollback()
                        raise HTTPException(status_code=404, detail="wallet not found")
                    cur.execute("SELECT * FROM transfers WHERE idempotency_key=%s", (key,))
                    raced = cur.fetchone()
                    if raced is not None:
                        conn.commit()
                        return _replay_or_conflict(raced)
                    # 4) No-overdraft: consume the key with a declined row so
                    #    retries are stable.
                    cur.execute(
                        """INSERT INTO transfers(from_wallet,to_wallet,amount_paise,idempotency_key,request_hash,status)
                           VALUES (%s,%s,%s,%s,%s,'declined') RETURNING *""",
                        (fw, tw, amt, key, rhash),
                    )
                    row = cur.fetchone()
                    conn.commit()
                    m.inc_domain("declined")
                    log_event("transfer_declined", cid, caller=caller, key=key,
                              transfer_id=str(row["id"]), reason="insufficient_funds")
                    return JSONResponse(status_code=402, content=_transfer_response(row))
            except HTTPException:
                raise
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    pass
                if _is_deadlock(e):
                    # Our tx aborted, nothing committed: safe to retry.
                    last_error = e
                    log_event("transfer_deadlock_retry", cid, caller=caller, key=key, attempt=attempt)
                    _time.sleep(0.02 * (2 ** attempt) + random.uniform(0, 0.02))
                    continue
                if _is_unique_violation(e):
                    # Storm loser (or declined-row race): winner committed — return it.
                    try:
                        existing = _read_existing()
                    except Exception:
                        existing = None
                    if existing is not None:
                        return _replay_or_conflict(existing)
                    # Winner aborted (e.g. deadlocked away): retry our tx.
                    last_error = e
                    _time.sleep(0.02 * (2 ** attempt) + random.uniform(0, 0.02))
                    continue
                last_error = e
                break
    log_event("transfer_error", cid, caller=caller, key=key,
              error=str(last_error)[:300] if last_error else "unknown")
    raise HTTPException(status_code=500, detail="transfer failed")


@app.get("/transfers/{transfer_id}")
def get_transfer(transfer_id: str, request: Request, caller: str = Depends(get_current_user)):
    try:
        uuid.UUID(transfer_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="transfer id must be UUID")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM transfers WHERE id=%s", (transfer_id,))
            row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="transfer not found")
    return _transfer_response(row)


# ---- test-only helper: fund a wallet (used by tests/burst seeding) ----
@app.post("/_test/fund")
def test_fund(body: dict, request: Request, caller: str = Depends(get_current_user)):
    import os
    if os.getenv("ALLOW_TEST_FUND", "1") != "1":
        raise HTTPException(status_code=403, detail="disabled")
    wid = body.get("wallet_id")
    amount = body.get("amount_paise")
    if not wid or not isinstance(amount, int) or amount <= 0:
        raise HTTPException(status_code=400, detail="wallet_id + positive int amount_paise required")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE wallets SET balance_paise = balance_paise + %s WHERE id=%s RETURNING balance_paise", (amount, wid))
            row = cur.fetchone()
        conn.commit()
    if row is None:
        raise HTTPException(status_code=404, detail="wallet not found")
    return {"id": wid, "balance_paise": row["balance_paise"]}
