"""Invitation code Pydantic models."""

from datetime import datetime
from pydantic import BaseModel, Field


class CreateInvitationRequest(BaseModel):
    code: str | None = Field(
        None,
        max_length=64,
        description="Custom code string. Auto-generated if omitted.",
    )
    max_uses: int = Field(1, ge=1, le=10000, description="Max number of redemptions.")
    expires_in_hours: int | None = Field(
        None,
        ge=1,
        le=8760,
        description="Hours until expiry. None = never expires.",
    )
    note: str = Field("", max_length=200, description="Admin note for this code.")


class InvitationPublic(BaseModel):
    id: str
    code: str
    created_by: str
    max_uses: int
    current_uses: int
    is_active: bool
    expires_at: datetime | None
    note: str
    created_at: datetime


class InvitationListResponse(BaseModel):
    codes: list[InvitationPublic]
    total: int
