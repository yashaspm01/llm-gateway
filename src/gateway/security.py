"""API key generation and hashing."""

import hashlib
import hmac
import secrets

KEY_PREFIX = "gw_"


def hash_api_key(key: str, pepper: str) -> str:
    """Keyed hash (HMAC-SHA256). The pepper lives in config, never in the database."""
    return hmac.new(pepper.encode(), key.encode(), hashlib.sha256).hexdigest()


def generate_api_key(pepper: str) -> tuple[str, str, str]:
    """Return (plaintext_key, display_prefix, key_hash). Only the last two are stored."""
    plaintext = f"{KEY_PREFIX}{secrets.token_urlsafe(32)}"
    return plaintext, plaintext[:10], hash_api_key(plaintext, pepper)
