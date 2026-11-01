"""Invitation code management — create, list, revoke, validate."""

from datetime import datetime, timedelta, timezone
from bson import ObjectId

from app.database import get_db
from app.utils.security import generate_invite_code


async def create_invitation(
    created_by: str,
    code: str | None = None,
    max_uses: int = 1,
    expires_in_hours: int | None = None,
    note: str = "",
) -> dict:
    """Create a new invitation code. Returns the document."""
    db = get_db()
    now = datetime.now(timezone.utc)

    code_str = code.strip() if code else generate_invite_code()

    doc = {
        "code": code_str,
        "created_by": created_by,
        "max_uses": max_uses,
        "current_uses": 0,
        "is_active": True,
        "expires_at": (
            now + timedelta(hours=expires_in_hours) if expires_in_hours else None
        ),
        "note": note.strip(),
        "created_at": now,
    }

    from pymongo.errors import DuplicateKeyError
    try:
        result = await db.invitation_codes.insert_one(doc)
    except DuplicateKeyError:
        raise ValueError(f"Invitation code '{code_str}' already exists.")

    doc["_id"] = result.inserted_id
    return _serialize(doc)


async def list_invitations(include_inactive: bool = False) -> list[dict]:
    """List all invitation codes. Optionally include revoked ones."""
    db = get_db()
    query = {} if include_inactive else {"is_active": True}
    cursor = db.invitation_codes.find(query).sort("created_at", -1)
    docs = await cursor.to_list(length=500)
    return [_serialize(d) for d in docs]


async def revoke_invitation(code_id: str) -> bool:
    """Deactivate an invitation code."""
    db = get_db()
    result = await db.invitation_codes.update_one(
        {"_id": ObjectId(code_id)},
        {"$set": {"is_active": False}},
    )
    return result.modified_count > 0


async def ensure_initial_code(code: str) -> None:
    """
    Create the initial invitation code from env var if no codes exist.
    Called once at startup.
    """
    db = get_db()
    count = await db.invitation_codes.count_documents({})
    if count == 0:
        await create_invitation(
            created_by="system",
            code=code,
            max_uses=10,
            expires_in_hours=None,
            note="Initial bootstrap code (created at first startup)",
        )


def _serialize(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "code": doc["code"],
        "created_by": doc["created_by"],
        "max_uses": doc["max_uses"],
        "current_uses": doc.get("current_uses", 0),
        "is_active": doc.get("is_active", True),
        "expires_at": doc.get("expires_at"),
        "note": doc.get("note", ""),
        "created_at": doc["created_at"],
    }
