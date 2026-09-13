from typing import Optional
from pydantic import BaseModel, field_validator


class TokenRequest(BaseModel):
    user_id: str


class CreateWalletRequest(BaseModel):
    user_id: str


class TransferRequest(BaseModel):
    from_wallet: str
    to_wallet: str
    amount_paise: int
    idempotency_key: str

    @field_validator("amount_paise", mode="before")
    @classmethod
    def must_be_int_paise(cls, v):
        # Reject floats / decimal strings — money is integer paise only.
        if isinstance(v, bool):
            raise ValueError("amount_paise must be integer paise")
        if isinstance(v, float):
            raise ValueError("amount_paise must be integer paise, never float")
        if isinstance(v, str):
            # allow "100" but not "10.5"
            s = v.strip()
            if "." in s:
                raise ValueError("amount_paise must be integer paise, never rupees-decimal")
            try:
                v = int(s)
            except ValueError:
                raise ValueError("amount_paise must be integer paise")
        if not isinstance(v, int):
            raise ValueError("amount_paise must be integer paise")
        if v <= 0:
            raise ValueError("amount_paise must be > 0")
        return v

    @field_validator("idempotency_key")
    @classmethod
    def key_nonempty(cls, v):
        if not v or not v.strip():
            raise ValueError("idempotency_key required")
        return v
