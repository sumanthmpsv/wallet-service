import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://wallet:wallet@localhost:5432/wallet",
)
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-in-prod")
JWT_ALG = os.getenv("JWT_ALG", "HS256")
JWT_EXP_MINUTES = int(os.getenv("JWT_EXP_MINUTES", "1440"))
