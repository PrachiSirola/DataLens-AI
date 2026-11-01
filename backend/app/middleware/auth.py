"""Authentication middleware — cookie-based session verification."""

from fastapi import Request, HTTPException, Depends
from app.services.auth_service import get_session_user

SESSION_COOKIE = "session_id"


async def get_current_user(request: Request) -> dict:
    """
    Dependency that extracts the session cookie, validates it,
    and returns the authenticated user dict.
    Raises 401 if unauthenticated.
    """
    session_id = request.cookies.get(SESSION_COOKIE)
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    user = await get_session_user(session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session expired or invalid.")
    return user


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Dependency that ensures the authenticated user has admin role."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user
