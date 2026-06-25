"""Fernet helpers for secrets stored in the database."""

from cryptography.fernet import Fernet, InvalidToken

from .config import settings


class CryptoError(RuntimeError):
    """Raised when encryption settings or ciphertext are invalid."""


def _fernet() -> Fernet:
    key = settings.encryption_key.strip()
    if not key:
        raise CryptoError("ENCRYPTION_KEY is required for provider API key encryption")
    try:
        return Fernet(key.encode())
    except ValueError as e:
        raise CryptoError("ENCRYPTION_KEY must be a valid Fernet key") from e


def encrypt_secret(value: str) -> str:
    """Encrypt a plaintext secret for DB storage."""
    if not value:
        raise CryptoError("secret value cannot be empty")
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    """Decrypt a DB-stored secret."""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as e:
        raise CryptoError("stored provider API key cannot be decrypted") from e
