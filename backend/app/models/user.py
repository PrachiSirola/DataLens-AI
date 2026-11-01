"""User-related Pydantic models (request/response schemas)."""

from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


# ── Request schemas ──────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=100)
    invitation_code: str = Field(min_length=1, max_length=64)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


# ── Response schemas ─────────────────────────────────────────────

class UserPublic(BaseModel):
    """Safe user representation (never exposes password hash)."""
    id: str
    email: str
    name: str
    role: str
    created_at: datetime


class AuthResponse(BaseModel):
    user: UserPublic
    message: str = "Success"
