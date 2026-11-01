"""MongoDB async connection via Motor."""

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from app.config import settings

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


async def connect_db() -> None:
    """Open the Motor client and set up indexes."""
    global _client, _db
    _client = AsyncIOMotorClient(settings.mongodb_uri)
    _db = _client[settings.database_name]

    # Unique indexes
    await _db.users.create_index("email", unique=True)
    await _db.sessions.create_index("session_id", unique=True)
    await _db.sessions.create_index("expires_at", expireAfterSeconds=0)  # TTL
    await _db.invitation_codes.create_index("code", unique=True)


async def close_db() -> None:
    """Close the Motor client."""
    global _client, _db
    if _client:
        _client.close()
    _client = None
    _db = None


def get_db() -> AsyncIOMotorDatabase:
    """Return the database handle. Raises if not connected."""
    if _db is None:
        raise RuntimeError("Database not connected. Call connect_db() first.")
    return _db
