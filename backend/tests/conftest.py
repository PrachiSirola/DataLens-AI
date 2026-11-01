"""
Shared fixtures for auth tests.

Uses mongomock_motor to provide an in-memory MongoDB that
behaves like a real Motor client — no external server needed.
"""

import os, asyncio
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from mongomock_motor import AsyncMongoMockClient

# ── Ensure test env vars are set BEFORE importing app code ──────
os.environ.update({
    "MONGODB_URI": "mongodb://localhost:27017",  # won't actually connect
    "DATABASE_NAME": "test_datalensai",
    "SESSION_SECRET_KEY": "test_secret_key_that_is_at_least_32_chars_long_ok",
    "SESSION_EXPIRE_HOURS": "24",
    "ADMIN_EMAIL": "admin@testanalyst.com",
    "ADMIN_PASSWORD": "TestAdmin#2026",
    "ADMIN_NAME": "Test Admin",
    "INITIAL_INVITE_CODE": "TEST-INVITE-CODE",
    "HOST": "0.0.0.0",
    "PORT": "8000",
    "CORS_ORIGINS": "http://localhost:5173",
    "OLLAMA_BASE_URL": "http://localhost:11434",
    "OLLAMA_MODEL": "qwen2.5-coder:1.5b",
})


@pytest_asyncio.fixture()
async def mock_db():
    """
    Replace app.database internals with a mongomock_motor client.
    Each test gets a fresh, empty database.
    """
    import app.database as db_mod

    client = AsyncMongoMockClient()
    database = client["test_datalensai"]

    # Patch the module-level singletons
    db_mod._client = client
    db_mod._db = database

    # Create indexes just like the real connect_db
    await database.users.create_index("email", unique=True)
    await database.sessions.create_index("session_id", unique=True)
    await database.invitation_codes.create_index("code", unique=True)

    yield database

    # Cleanup
    client.close()
    db_mod._client = None
    db_mod._db = None


@pytest_asyncio.fixture()
async def seeded_db(mock_db):
    """
    mock_db + bootstrap invitation code + admin user.
    Mirrors what main.py lifespan does.
    """
    from app.utils.security import hash_password
    from app.services.invitation_service import ensure_initial_code

    await ensure_initial_code("TEST-INVITE-CODE")

    # Bootstrap admin
    now = datetime.now(timezone.utc)
    await mock_db.users.insert_one({
        "email": "admin@testanalyst.com",
        "password_hash": hash_password("TestAdmin#2026"),
        "name": "Test Admin",
        "role": "admin",
        "is_active": True,
        "invited_by_code": "bootstrap",
        "created_at": now,
        "updated_at": now,
    })

    return mock_db


@pytest_asyncio.fixture()
async def client(seeded_db):
    """
    httpx AsyncClient wired to the FastAPI app.
    Bypasses lifespan (we already seeded the DB).
    """
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as ac:
        yield ac


@pytest_asyncio.fixture()
async def authed_client(client):
    """Client already logged in as admin."""
    resp = await client.post("/api/auth/login", json={
        "email": "admin@testanalyst.com",
        "password": "TestAdmin#2026",
    })
    assert resp.status_code == 200
    # httpx carries cookies forward automatically
    return client


@pytest_asyncio.fixture()
async def user_client(client, seeded_db):
    """Client registered and logged in as a regular user."""
    resp = await client.post("/api/auth/register", json={
        "email": "user@testanalyst.com",
        "password": "UserPass#2026",
        "name": "Test User",
        "invitation_code": "TEST-INVITE-CODE",
    })
    assert resp.status_code == 200
    return client
