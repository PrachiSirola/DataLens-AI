"""Authentication routes — register, login, logout, me."""

from fastapi import APIRouter, HTTPException, Response, Request, Depends
from app.models.user import RegisterRequest, LoginRequest, UserPublic, AuthResponse
from app.services.auth_service import (
    register_user,
    authenticate_user,
    create_session,
    delete_session,
)
from app.middleware.auth import get_current_user, SESSION_COOKIE
from app.config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

_COOKIE_OPTS = {
    "httponly": True,
    "samesite": "lax",
    "secure": False,  # Set True in production with HTTPS
    "max_age": settings.session_expire_hours * 3600,
    "path": "/",
}


@router.post("/register", response_model=AuthResponse)
async def register(body: RegisterRequest, response: Response):
    """Register a new account using an invitation code."""
    try:
        user = await register_user(
            email=body.email,
            password=body.password,
            name=body.name,
            invitation_code=body.invitation_code,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    session_id = await create_session(user["id"])
    response.set_cookie(SESSION_COOKIE, session_id, **_COOKIE_OPTS)

    return AuthResponse(
        user=UserPublic(**user),
        message="Account created successfully.",
    )


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest, response: Response):
    """Log in with email + password."""
    try:
        user = await authenticate_user(body.email, body.password)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    session_id = await create_session(user["id"])
    response.set_cookie(SESSION_COOKIE, session_id, **_COOKIE_OPTS)

    return AuthResponse(
        user=UserPublic(**user),
        message="Logged in successfully.",
    )


@router.post("/logout")
async def logout(request: Request, response: Response):
    """Log out — delete server session and clear cookie."""
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id:
        await delete_session(session_id)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"message": "Logged out."}


@router.get("/me", response_model=UserPublic)
async def me(user: dict = Depends(get_current_user)):
    """Return the currently authenticated user."""
    return UserPublic(**user)
