"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import connect_db, close_db
from app.routes import auth, admin, dashboard, datasets, query
from app.services.invitation_service import ensure_initial_code


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    # ── Startup ──
    await connect_db()
    await ensure_initial_code(settings.initial_invite_code)
    print(f"✓ MongoDB connected: {settings.database_name}")
    print(f"✓ Initial invite code ensured")

    # Bootstrap admin if not exists
    from app.database import get_db
    from app.utils.security import hash_password
    from datetime import datetime, timezone

    db = get_db()
    admin_exists = await db.users.find_one({"email": settings.admin_email.lower()})
    if not admin_exists:
        now = datetime.now(timezone.utc)
        await db.users.insert_one({
            "email": settings.admin_email.lower().strip(),
            "password_hash": hash_password(settings.admin_password),
            "name": settings.admin_name,
            "role": "admin",
            "is_active": True,
            "invited_by_code": "bootstrap",
            "created_at": now,
            "updated_at": now,
        })
        print(f"✓ Admin user bootstrapped: {settings.admin_email}")
    else:
        print(f"✓ Admin user exists: {settings.admin_email}")

    yield

    # ── Shutdown ──
    await close_db()
    print("✓ MongoDB disconnected")


app = FastAPI(
    title="DataLensAI",
    description="Upload CSV/PDF/DOCX files, ask questions in natural language, get accurate analysis powered by local AI.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS for the React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount route groups
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(dashboard.router)
app.include_router(datasets.router)
app.include_router(query.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "datalensai"}
