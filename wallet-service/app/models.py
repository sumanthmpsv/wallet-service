import hashlib


def request_hash(from_wallet: str, to_wallet: str, amount_paise: int) -> str:
    h = hashlib.sha256()
    h.update(f"{from_wallet}|{to_wallet}|{amount_paise}".encode())
    return h.hexdigest()
