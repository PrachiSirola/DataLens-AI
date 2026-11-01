"""
End-to-end auth tests covering:
  1. Registration with valid invitation code
  2. Registration with invalid / expired / exhausted code
  3. Duplicate email registration
  4. Login with correct credentials
  5. Login with wrong password
  6. Login with non-existent email
  7. Session cookie set/cleared properly
  8. GET /me returns user when authenticated
  9. GET /me returns 401 when not authenticated
 10. Logout clears session
 11. Protected dashboard route requires auth
 12. Admin routes reject non-admin users
 13. Expired session rejected
"""

import pytest
from datetime import datetime, timedelta, timezone

pytestmark = pytest.mark.asyncio


# ═══════════════════════════════════════════════════════════════════
#  REGISTRATION
# ═══════════════════════════════════════════════════════════════════

class TestRegistration:

    async def test_register_with_valid_code(self, client):
        """New user can register with a valid invitation code."""
        resp = await client.post("/api/auth/register", json={
            "email": "new@example.com",
            "password": "SecurePass123",
            "name": "New User",
            "invitation_code": "TEST-INVITE-CODE",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["user"]["email"] == "new@example.com"
        assert data["user"]["name"] == "New User"
        assert data["user"]["role"] == "user"
        assert data["message"] == "Account created successfully."
        # Session cookie must be set
        assert "session_id" in resp.cookies

    async def test_register_with_invalid_code(self, client):
        """Registration fails with a nonexistent invitation code."""
        resp = await client.post("/api/auth/register", json={
            "email": "bad@example.com",
            "password": "SecurePass123",
            "name": "Bad User",
            "invitation_code": "INVALID-CODE-999",
        })
        assert resp.status_code == 400
        assert "Invalid invitation code" in resp.json()["detail"]

    async def test_register_duplicate_email(self, client):
        """Cannot register twice with the same email."""
        payload = {
            "email": "dup@example.com",
            "password": "SecurePass123",
            "name": "First",
            "invitation_code": "TEST-INVITE-CODE",
        }
        resp1 = await client.post("/api/auth/register", json=payload)
        assert resp1.status_code == 200

        payload["name"] = "Second"
        resp2 = await client.post("/api/auth/register", json=payload)
        assert resp2.status_code == 400
        assert "already exists" in resp2.json()["detail"]

    async def test_register_with_revoked_code(self, client, seeded_db):
        """Registration fails with a deactivated invitation code."""
        # Revoke the code
        await seeded_db.invitation_codes.update_one(
            {"code": "TEST-INVITE-CODE"},
            {"$set": {"is_active": False}},
        )
        resp = await client.post("/api/auth/register", json={
            "email": "revoked@example.com",
            "password": "SecurePass123",
            "name": "Revoked",
            "invitation_code": "TEST-INVITE-CODE",
        })
        assert resp.status_code == 400
        assert "deactivated" in resp.json()["detail"]

    async def test_register_with_expired_code(self, client, seeded_db):
        """Registration fails with an expired invitation code."""
        # Set expiry to the past (use naive UTC — mongomock stores naive datetimes)
        await seeded_db.invitation_codes.update_one(
            {"code": "TEST-INVITE-CODE"},
            {"$set": {"expires_at": datetime.utcnow() - timedelta(hours=1)}},
        )
        resp = await client.post("/api/auth/register", json={
            "email": "expired@example.com",
            "password": "SecurePass123",
            "name": "Expired",
            "invitation_code": "TEST-INVITE-CODE",
        })
        assert resp.status_code == 400
        assert "expired" in resp.json()["detail"]

    async def test_register_with_exhausted_code(self, client, seeded_db):
        """Registration fails when invitation code has reached its usage limit."""
        # Set current_uses = max_uses
        await seeded_db.invitation_codes.update_one(
            {"code": "TEST-INVITE-CODE"},
            {"$set": {"current_uses": 10, "max_uses": 10}},
        )
        resp = await client.post("/api/auth/register", json={
            "email": "exhausted@example.com",
            "password": "SecurePass123",
            "name": "Exhausted",
            "invitation_code": "TEST-INVITE-CODE",
        })
        assert resp.status_code == 400
        assert "usage limit" in resp.json()["detail"]

    async def test_register_short_password(self, client):
        """Password under 8 chars is rejected by Pydantic validation."""
        resp = await client.post("/api/auth/register", json={
            "email": "short@example.com",
            "password": "abc",
            "name": "Short",
            "invitation_code": "TEST-INVITE-CODE",
        })
        assert resp.status_code == 422  # Pydantic validation error

    async def test_register_empty_name(self, client):
        """Empty name is rejected."""
        resp = await client.post("/api/auth/register", json={
            "email": "noname@example.com",
            "password": "SecurePass123",
            "name": "",
            "invitation_code": "TEST-INVITE-CODE",
        })
        assert resp.status_code == 422

    async def test_register_invalid_email(self, client):
        """Invalid email format is rejected."""
        resp = await client.post("/api/auth/register", json={
            "email": "not-an-email",
            "password": "SecurePass123",
            "name": "Bad Email",
            "invitation_code": "TEST-INVITE-CODE",
        })
        assert resp.status_code == 422

    async def test_register_increments_code_usage(self, client, seeded_db):
        """Successful registration increments the invitation code's current_uses."""
        code_before = await seeded_db.invitation_codes.find_one({"code": "TEST-INVITE-CODE"})
        uses_before = code_before["current_uses"]

        await client.post("/api/auth/register", json={
            "email": "counter@example.com",
            "password": "SecurePass123",
            "name": "Counter",
            "invitation_code": "TEST-INVITE-CODE",
        })

        code_after = await seeded_db.invitation_codes.find_one({"code": "TEST-INVITE-CODE"})
        assert code_after["current_uses"] == uses_before + 1


# ═══════════════════════════════════════════════════════════════════
#  LOGIN
# ═══════════════════════════════════════════════════════════════════

class TestLogin:

    async def test_login_admin(self, client):
        """Admin can log in with correct credentials."""
        resp = await client.post("/api/auth/login", json={
            "email": "admin@testanalyst.com",
            "password": "TestAdmin#2026",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["user"]["email"] == "admin@testanalyst.com"
        assert data["user"]["role"] == "admin"
        assert "session_id" in resp.cookies

    async def test_login_wrong_password(self, client):
        """Login fails with wrong password."""
        resp = await client.post("/api/auth/login", json={
            "email": "admin@testanalyst.com",
            "password": "WrongPassword!",
        })
        assert resp.status_code == 401
        assert "Invalid email or password" in resp.json()["detail"]

    async def test_login_nonexistent_email(self, client):
        """Login fails with an email that doesn't exist."""
        resp = await client.post("/api/auth/login", json={
            "email": "nobody@example.com",
            "password": "SecurePass123",
        })
        assert resp.status_code == 401
        assert "Invalid email or password" in resp.json()["detail"]

    async def test_login_deactivated_user(self, client, seeded_db):
        """Login fails if the user account has been deactivated."""
        # Register a user, then deactivate them
        await client.post("/api/auth/register", json={
            "email": "deactivated@example.com",
            "password": "SecurePass123",
            "name": "Deactivated",
            "invitation_code": "TEST-INVITE-CODE",
        })
        await seeded_db.users.update_one(
            {"email": "deactivated@example.com"},
            {"$set": {"is_active": False}},
        )

        resp = await client.post("/api/auth/login", json={
            "email": "deactivated@example.com",
            "password": "SecurePass123",
        })
        assert resp.status_code == 401
        assert "deactivated" in resp.json()["detail"]

    async def test_login_email_case_insensitive(self, client):
        """Login should be case-insensitive for email."""
        resp = await client.post("/api/auth/login", json={
            "email": "Admin@TestAnalyst.com",
            "password": "TestAdmin#2026",
        })
        assert resp.status_code == 200
        assert resp.json()["user"]["email"] == "admin@testanalyst.com"


# ═══════════════════════════════════════════════════════════════════
#  SESSION / AUTHENTICATION
# ═══════════════════════════════════════════════════════════════════

class TestSession:

    async def test_me_authenticated(self, authed_client):
        """GET /me returns current user when authenticated."""
        resp = await authed_client.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "admin@testanalyst.com"
        assert data["role"] == "admin"
        # Must NOT contain password hash
        assert "password_hash" not in data
        assert "password" not in data

    async def test_me_unauthenticated(self, client):
        """GET /me returns 401 without a session cookie."""
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 401

    async def test_me_with_garbage_cookie(self, client):
        """GET /me returns 401 with an invalid session_id."""
        client.cookies.set("session_id", "totally-fake-session-id-12345")
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 401

    async def test_logout_clears_session(self, authed_client):
        """POST /logout clears the session cookie and invalidates the session."""
        # First confirm we're logged in
        me_resp = await authed_client.get("/api/auth/me")
        assert me_resp.status_code == 200

        # Logout
        logout_resp = await authed_client.post("/api/auth/logout")
        assert logout_resp.status_code == 200
        assert "Logged out" in logout_resp.json()["message"]

        # After logout, /me should return 401
        me_resp2 = await authed_client.get("/api/auth/me")
        assert me_resp2.status_code == 401

    async def test_expired_session_rejected(self, client, seeded_db):
        """A session that has expired is rejected on /me."""
        # Login to get a session
        login_resp = await client.post("/api/auth/login", json={
            "email": "admin@testanalyst.com",
            "password": "TestAdmin#2026",
        })
        assert login_resp.status_code == 200
        session_id = login_resp.cookies.get("session_id")

        # Manually expire the session in DB (naive UTC for mongomock)
        await seeded_db.sessions.update_one(
            {"session_id": session_id},
            {"$set": {"expires_at": datetime.utcnow() - timedelta(hours=1)}},
        )

        # Now /me should fail
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════
#  PROTECTED ROUTES
# ═══════════════════════════════════════════════════════════════════

class TestProtectedRoutes:

    async def test_dashboard_requires_auth(self, client):
        """GET /dashboard/ returns 401 without authentication."""
        resp = await client.get("/api/dashboard/")
        assert resp.status_code == 401

    async def test_dashboard_accessible_when_authed(self, authed_client):
        """GET /dashboard/ works when authenticated."""
        resp = await authed_client.get("/api/dashboard/")
        assert resp.status_code == 200

    async def test_admin_routes_require_admin_role(self, user_client):
        """Admin endpoints reject users without admin role."""
        resp = await user_client.get("/api/admin/invitations")
        assert resp.status_code == 403
        assert "Admin access required" in resp.json()["detail"]

    async def test_admin_create_invitation_requires_admin(self, user_client):
        """POST /admin/invitations rejects non-admin users."""
        resp = await user_client.post("/api/admin/invitations", json={
            "max_uses": 5,
            "note": "Should fail",
        })
        assert resp.status_code == 403

    async def test_admin_revoke_requires_admin(self, user_client):
        """PATCH /admin/invitations/{id}/revoke rejects non-admin users."""
        resp = await user_client.patch("/api/admin/invitations/000000000000000000000000/revoke")
        assert resp.status_code == 403

    async def test_admin_routes_require_auth(self, client):
        """Admin endpoints return 401 without any authentication."""
        resp = await client.get("/api/admin/invitations")
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════
#  ADMIN INVITATION MANAGEMENT
# ═══════════════════════════════════════════════════════════════════

class TestAdminInvitations:

    async def test_create_invitation(self, authed_client):
        """Admin can create a new invitation code."""
        resp = await authed_client.post("/api/admin/invitations", json={
            "max_uses": 5,
            "expires_in_hours": 48,
            "note": "For new hires",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["max_uses"] == 5
        assert data["current_uses"] == 0
        assert data["is_active"] is True
        assert data["note"] == "For new hires"
        assert data["expires_at"] is not None
        # Auto-generated code should be in XXXX-XXXX-XXXX format
        assert len(data["code"]) >= 8

    async def test_create_custom_code(self, authed_client):
        """Admin can create an invitation with a custom code."""
        resp = await authed_client.post("/api/admin/invitations", json={
            "code": "CUSTOM-TEAM-2026",
            "max_uses": 10,
            "note": "Custom code test",
        })
        assert resp.status_code == 200
        assert resp.json()["code"] == "CUSTOM-TEAM-2026"

    async def test_create_duplicate_code(self, authed_client):
        """Creating a code that already exists fails."""
        payload = {
            "code": "UNIQUE-CODE-1",
            "max_uses": 1,
        }
        resp1 = await authed_client.post("/api/admin/invitations", json=payload)
        assert resp1.status_code == 200

        resp2 = await authed_client.post("/api/admin/invitations", json=payload)
        assert resp2.status_code == 400
        assert "already exists" in resp2.json()["detail"]

    async def test_list_invitations(self, authed_client):
        """Admin can list all invitation codes."""
        resp = await authed_client.get("/api/admin/invitations?include_inactive=true")
        assert resp.status_code == 200
        data = resp.json()
        assert "codes" in data
        assert "total" in data
        # At least the bootstrap code should exist
        assert data["total"] >= 1

    async def test_revoke_invitation(self, authed_client, seeded_db):
        """Admin can revoke an invitation code."""
        # Create one to revoke
        create_resp = await authed_client.post("/api/admin/invitations", json={
            "code": "TO-REVOKE-1",
            "max_uses": 5,
        })
        code_id = create_resp.json()["id"]

        # Revoke it
        revoke_resp = await authed_client.patch(f"/api/admin/invitations/{code_id}/revoke")
        assert revoke_resp.status_code == 200
        assert "revoked" in revoke_resp.json()["message"].lower()

        # Verify it's deactivated in DB
        doc = await seeded_db.invitation_codes.find_one({"code": "TO-REVOKE-1"})
        assert doc["is_active"] is False

    async def test_revoke_nonexistent_code(self, authed_client):
        """Revoking a nonexistent code returns 404."""
        resp = await authed_client.patch("/api/admin/invitations/000000000000000000000000/revoke")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════
#  HEALTH ENDPOINT
# ═══════════════════════════════════════════════════════════════════

class TestHealth:

    async def test_health_check(self, client):
        """Health endpoint is public and returns ok."""
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
