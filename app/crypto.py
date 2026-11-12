"""Encrypt per-client Vision One API keys at rest with a Fernet master key from the environment."""
from cryptography.fernet import Fernet, InvalidToken

from .config import settings


def generate_key() -> str:
    return Fernet.generate_key().decode()


def _fernet() -> Fernet:
    if not settings.master_key:
        raise RuntimeError("MASTER_KEY is not set; generate one with `python -m app.cli gen-key`")
    return Fernet(settings.master_key.encode())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("Could not decrypt API key: MASTER_KEY changed or data is corrupt") from exc
