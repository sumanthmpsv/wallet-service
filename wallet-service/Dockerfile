# ---- build ----
FROM python:3.13-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- runtime ----
FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl \
  && rm -rf /var/lib/apt/lists/* \
  && useradd -m -u 10001 appuser
WORKDIR /app
COPY --from=builder /install /usr/local
COPY app ./app
# migrations/ is kept for reference; tables are created via app/db.py SCHEMA_SQL
USER appuser
EXPOSE 8000
# Render sets $PORT (default 10000); fall back to 8000 locally.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD sh -c 'curl -f http://localhost:${PORT:-8000}/health || exit 1'
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
