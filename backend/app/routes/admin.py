"""Admin routes — invitation code management."""

from fastapi import APIRouter, HTTPException, Depends
from app.models.invitation import (
    CreateInvitationRequest,
    InvitationPublic,
    InvitationListResponse,
)
from app.services.invitation_service import (
    create_invitation,
    list_invitations,
    revoke_invitation,
)
from app.middleware.auth import require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/invitations", response_model=InvitationPublic)
async def create_code(
    body: CreateInvitationRequest, admin: dict = Depends(require_admin)
):
    """Create a new invitation code (admin only)."""
    try:
        doc = await create_invitation(
            created_by=admin["email"],
            code=body.code,
            max_uses=body.max_uses,
            expires_in_hours=body.expires_in_hours,
            note=body.note,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return InvitationPublic(**doc)


@router.get("/invitations", response_model=InvitationListResponse)
async def list_codes(
    include_inactive: bool = False, admin: dict = Depends(require_admin)
):
    """List all invitation codes (admin only)."""
    codes = await list_invitations(include_inactive=include_inactive)
    return InvitationListResponse(
        codes=[InvitationPublic(**c) for c in codes],
        total=len(codes),
    )


@router.patch("/invitations/{code_id}/revoke")
async def revoke_code(code_id: str, admin: dict = Depends(require_admin)):
    """Revoke (deactivate) an invitation code (admin only)."""
    ok = await revoke_invitation(code_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Code not found.")
    return {"message": "Invitation code revoked."}
