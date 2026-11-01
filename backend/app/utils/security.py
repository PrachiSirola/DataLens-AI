"""Password hashing and session token utilities."""

import secrets
from passlib.context import CryptContext

# bcrypt with automatic salt — the gold standard for password storage
_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt."""
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against its bcrypt hash."""
    return _pwd_ctx.verify(plain, hashed)


def generate_session_id() -> str:
    """Generate a cryptographically secure session token (64 hex chars)."""
    return secrets.token_hex(32)


def generate_invite_code() -> str:
    """Generate a human-friendly invite code like 'ABCD-EFGH-1234'."""
    seg1 = secrets.token_hex(2).upper()
    seg2 = secrets.token_hex(2).upper()
    seg3 = secrets.token_hex(2).upper()
    return f"{seg1}-{seg2}-{seg3}"
