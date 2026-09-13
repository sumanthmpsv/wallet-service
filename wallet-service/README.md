# Wallet & P2P Transfer (Paytm R2 exercise)

FastAPI + Postgres. Money in integer paise. JWT bearer auth (`sub` = caller).

## Run locally (one command)
```bash
cp .env.example .env   # set JWT_SECRET
docker compose up --build
# API: http://localhost:8000  health: /health  metrics: /metrics
```

## Test (TDD)
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
DATABASE_URL=postgresql://wallet:wallet@localhost:5432/wallet \
JWT_SECRET=test-secret .venv/bin/pytest -q
```

## Burst (all 3 live probes, one command)
```bash
.venv/bin/python burst.py --url http://localhost:8000 --n 50 --k 30 --m 200
```

## Auth
```bash
curl -X POST localhost:8000/auth/token -H 'Content-Type: application/json' \
  -d '{"user_id":"alice"}'   # -> access_token (JWT)
# use: Authorization: Bearer <token>
# POST /wallets {"user_id":"..."} is get-or-create (any valid JWT may act;
# tighten to body.user_id == sub for strict mode — one-line change).
```

## Deploy (Render + Neon, ₹0 free tiers)
1. Create free Neon project → copy `DATABASE_URL` (…?sslmode=require).
2. Render → New Web Service from repo (Docker runtime), set `DATABASE_URL`,
   `JWT_SECRET` (generate), `ALLOW_TEST_FUND=0`, health check `/health`.
3. Verify `/health`, `/metrics`, then `python burst.py --url https://<app>.onrender.com`.

## Endpoints
`POST /auth/token, POST /wallets, GET /wallets/{id}, POST /transfers,
GET /transfers/{id}, GET /health, GET /metrics, GET /metrics.json`
Overdraft → `402 {status:declined}`; key-reuse-different-body → `409`.
