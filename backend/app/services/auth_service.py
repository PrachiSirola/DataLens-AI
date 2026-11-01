"""Core authentication business logic — registration, login, sessions."""

from datetime import datetime, timedelta, timezone
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.config import settings
from app.database import get_db
from app.utils.security import hash_password, verify_password, generate_session_id


# ── User registration ────────────────────────────────────────────

async def register_user(
    email: str, password: str, name: str, invitation_code: str
) -> dict:
    """
    Register a new user after validating the invitation code.
    Returns the created user document (without password_hash).
    Raises ValueError on validation failures.
    """
    db = get_db()

    # 1. Validate invitation code
    code_doc = await db.invitation_codes.find_one({"code": invitation_code})
    if not code_doc:
        raise ValueError("Invalid invitation code.")
    if not code_doc.get("is_active", False):
        raise ValueError("This invitation code has been deactivated.")
    expires_at = code_doc.get("expires_at")
    if expires_at:
        now = datetime.now(timezone.utc)

        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < now:
            raise ValueError("This invitation code has expired.")
    if code_doc.get("current_uses", 0) >= code_doc.get("max_uses", 1):
        raise ValueError("This invitation code has reached its usage limit.")


    now = datetime.now(timezone.utc)
    user_doc = {
        "email": email.lower().strip(),
        "password_hash": hash_password(password),
        "name": name.strip(),
        "role": "user",
        "is_active": True,
        "invited_by_code": invitation_code,
        "created_at": now,
        "updated_at": now,
    }
    try:
        result = await db.users.insert_one(user_doc)
    except DuplicateKeyError:
        raise ValueError("An account with this email already exists.")


    await db.invitation_codes.update_one(
        {"_id": code_doc["_id"]},
        {"$inc": {"current_uses": 1}},
    )

    user_doc["_id"] = result.inserted_id
    return _sanitize_user(user_doc)

async def authenticate_user(email: str, password: str) -> dict:
    """
    Validate credentials and return the user document.
    Raises ValueError on failure.
    """
    db = get_db()
    user = await db.users.find_one({"email": email.lower().strip()})
    if not user:
        raise ValueError("Invalid email or password.")
    if not user.get("is_active", True):
        raise ValueError("This account has been deactivated.")
    if not verify_password(password, user["password_hash"]):
        raise ValueError("Invalid email or password.")
    return _sanitize_user(user)


async def create_session(user_id: str) -> str:
    """Create a server-side session and return the session_id token."""
    db = get_db()
    session_id = generate_session_id()
    now = datetime.now(timezone.utc)
    await db.sessions.insert_one({
        "session_id": session_id,
        "user_id": ObjectId(user_id),
        "created_at": now,
        "expires_at": now + timedelta(hours=settings.session_expire_hours),
    })
    return session_id


async def get_session_user(session_id: str) -> dict | None:
    """Look up session → user. Returns sanitized user or None."""
    db = get_db()
    session = await db.sessions.find_one({"session_id": session_id})
    if not session:
        return None
    session_expiry = session["expires_at"]
    if session_expiry.tzinfo is None:
        session_expiry = session_expiry.replace(tzinfo=timezone.utc)
    if session_expiry < datetime.now(timezone.utc):
        await db.sessions.delete_one({"_id": session["_id"]})
        return None
    user = await db.users.find_one({"_id": session["user_id"]})
    if not user or not user.get("is_active", True):
        return None
    return _sanitize_user(user)


async def delete_session(session_id: str) -> None:
    """Delete a session (logout)."""
    db = get_db()
    await db.sessions.delete_one({"session_id": session_id})


async def delete_all_user_sessions(user_id: str) -> None:
    """Nuke all sessions for a user (forced logout everywhere)."""
    db = get_db()
    await db.sessions.delete_many({"user_id": ObjectId(user_id)})


def _sanitize_user(doc: dict) -> dict:
    """Strip password_hash from user document and stringify _id."""
    return {
        "id": str(doc["_id"]),
        "email": doc["email"],
        "name": doc["name"],
        "role": doc.get("role", "user"),
        "is_active": doc.get("is_active", True),
        "created_at": doc["created_at"],
    }
