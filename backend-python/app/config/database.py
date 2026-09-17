from motor.motor_asyncio import AsyncIOMotorClient
from typing import Optional
from .settings import settings
import sys


def _safe_print(msg: str):
    """Print with fallback for Windows cp1252 terminals that can't handle emoji."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"))


class Database:
    client: Optional[AsyncIOMotorClient] = None
    database = None
    
    @classmethod
    async def connect_db(cls):
        try:
            cls.client = AsyncIOMotorClient(settings.MONGODB_URL)
            cls.database = cls.client[settings.DATABASE_NAME]
            await cls.client.admin.command('ping')
            _safe_print(f"[OK] MongoDB Connected: {settings.DATABASE_NAME}")
        except Exception as e:
            _safe_print(f"[ERROR] MongoDB Connection Error: {e}")
            raise e

        await cls._ensure_indexes()

    @classmethod
    async def _ensure_indexes(cls):
        """
        Create supporting indexes if they don't already exist. create_index()
        is idempotent, so this is safe to call on every startup.

        Index creation failures are logged but do NOT crash startup/raise:
        - Connectivity to Mongo (the thing that actually blocks the app from
          functioning) was already verified above via ping, so fail-fast
          there is appropriate.
        - A missing index is a degradation (slower queries, and for the
          users unique indexes, a reliance on the existing app-level
          check-then-insert race guard in auth_controller.py) rather than an
          outage, and the most likely real-world cause of failure - existing
          duplicate username/email values from legacy data - can't be fixed
          by retrying or crash-looping the process. Better to boot with a
          loud warning than to make the whole API unavailable over it.
        """
        try:
            users_collection = cls.database.get_collection("users")
            await users_collection.create_index("username", unique=True, name="uniq_username")
            await users_collection.create_index("email", unique=True, name="uniq_email")
            _safe_print("[OK] Ensured unique indexes on users.username and users.email")
        except Exception as e:
            _safe_print(
                "[WARN] Could not create unique indexes on users.username/email "
                f"({e}). This usually means duplicate username or email values "
                "already exist in the collection (e.g. legacy data). Manual "
                "data cleanup is required before this index can be built; "
                "until then, duplicate registrations are only guarded by the "
                "app-level check in auth_controller.py, which is race-prone "
                "under concurrent requests."
            )

        try:
            projects_collection = cls.database.get_collection("projects")
            await projects_collection.create_index("user_id", name="idx_projects_user_id")
            _safe_print("[OK] Ensured index on projects.user_id")
        except Exception as e:
            _safe_print(f"[WARN] Could not create index on projects.user_id ({e})")
    
    @classmethod
    async def close_db(cls):
        if cls.client:
            cls.client.close()
            _safe_print("[INFO] MongoDB Connection Closed")
    
    @classmethod
    def get_collection(cls, collection_name: str):
        if cls.database is None:
            raise Exception("Database not connected")
        return cls.database[collection_name]


db = Database()


def get_projects_collection():
    return db.get_collection("projects")
