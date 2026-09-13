"""Postgres helpers — raw SQL with explicit transactions (simplest-correct)."""
import psycopg
from psycopg.rows import dict_row
from .config import DATABASE_URL

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE TABLE IF NOT EXISTS wallets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id TEXT UNIQUE NOT NULL,
  balance_paise BIGINT NOT NULL DEFAULT 0 CHECK (balance_paise >= 0)
);
CREATE TABLE IF NOT EXISTS transfers (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  from_wallet UUID NOT NULL REFERENCES wallets(id),
  to_wallet UUID NOT NULL REFERENCES wallets(id),
  amount_paise BIGINT NOT NULL CHECK (amount_paise > 0),
  idempotency_key TEXT UNIQUE NOT NULL,
  request_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'completed' CHECK (status IN ('completed','declined')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_transfers_key ON transfers(idempotency_key);
"""


def get_conn():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
