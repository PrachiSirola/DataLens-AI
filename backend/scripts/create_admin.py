#!/usr/bin/env python3
"""
Bootstrap script — creates the admin user from env vars.
Run once: python -m scripts.create_admin
"""

import asyncio
import sys
import os

# Allow imports from parent
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import settings
from app.database import connect_db, get_db
from app.utils.security import hash_password
from datetime import datetime, timezone


async def main():
    await connect_db()
    db = get_db()

    existing = await db.users.find_one({"email": settings.admin_email.lower()})
    if existing:
        print(f"Admin '{settings.admin_email}' already exists. Skipping.")
        return

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
    print(f"Admin user created: {settings.admin_email}")


if __name__ == "__main__":
    asyncio.run(main())
