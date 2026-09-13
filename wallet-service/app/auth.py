"""JWT bearer auth. Token sub = user_id (caller identity)."""
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from .config import JWT_SECRET, JWT_ALG, JWT_EXP_MINUTES

bearer = HTTPBearer(auto_error=False)


def create_access_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": user_id, "iat": now, "exp": now + timedelta(minutes=JWT_EXP_MINUTES)}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> str:
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=[JWT_ALG])
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="invalid token: no sub")
        return str(sub)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="invalid token")
