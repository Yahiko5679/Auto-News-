"""
CosmicBotz — async MongoDB via Motor.

Collections:
  rss_feeds    : { _id(url), url, name, added_by, added_at, active }
  channels     : { _id(channel_id), channel_id, title, username, added_by, added_at }
  seen_entries : { _id(sha1_guid), published_at }  — 30-day TTL auto-dedup
  bot_stats    : singleton stats doc
  bot_settings : persistent runtime settings (poll_interval, limits, footer, etc.)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

# ── Default RSS feeds seeded on first run ─────────────────────────────────────
DEFAULT_FEEDS = [
    {
        "url": "https://feeds.feedburner.com/crunchyroll/rss",
        "name": "Crunchyroll News",
    },
    {
        "url": "https://www.animenewsnetwork.com/all/rss.xml?ann-edition=us",
        "name": "Anime News Network",
    },
]

# Default settings stored in DB (persisted across restarts)
DEFAULT_SETTINGS = {
    "poll_interval": 300,
    "max_rss": 25,
    "max_channels": 10,
    "disable_web_preview": False,
    "post_footer": "",
    "extra_admins": [],
}


class CosmicBotz:
    def __init__(self, uri: str, db_name: str):
        self._client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        self._db = self._client[db_name]

        self.rss      = self._db["rss_feeds"]
        self.channels = self._db["channels"]
        self.seen     = self._db["seen_entries"]
        self.stats    = self._db["bot_stats"]
        self.settings = self._db["bot_settings"]

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def init(self):
        """Create indexes once on startup."""
        await self.rss.create_index("url", unique=True)
        await self.channels.create_index("channel_id", unique=True)
        await self.seen.create_index("_id")
        await self.seen.create_index(
            "published_at", expireAfterSeconds=60 * 60 * 24 * 30
        )
        # Ensure default settings doc exists
        await self.settings.update_one(
            {"_id": "global"},
            {"$setOnInsert": {**DEFAULT_SETTINGS}},
            upsert=True,
        )
        logger.info("✅ CosmicBotz indexes and settings ready.")

    async def ping(self) -> bool:
        try:
            await self._client.admin.command("ping")
            return True
        except Exception:
            return False

    async def seed_defaults(self):
        """Seed default RSS feeds only if none exist yet."""
        count = await self.rss.count_documents({})
        if count > 0:
            return
        for feed in DEFAULT_FEEDS:
            try:
                await self.rss.insert_one({
                    "_id": feed["url"],
                    "url": feed["url"],
                    "name": feed["name"],
                    "added_by": 0,
                    "added_at": _now(),
                    "active": True,
                })
                logger.info(f"🌱 Seeded default feed: {feed['name']}")
            except Exception:
                pass

    # ── Settings (persistent, stored in MongoDB) ──────────────────────────────

    async def get_settings(self) -> Dict[str, Any]:
        doc = await self.settings.find_one({"_id": "global"}) or {}
        return {k: doc.get(k, v) for k, v in DEFAULT_SETTINGS.items()}

    async def set_setting(self, key: str, value: Any):
        await self.settings.update_one(
            {"_id": "global"},
            {"$set": {key: value}},
            upsert=True,
        )

    async def get_setting(self, key: str, default=None) -> Any:
        doc = await self.settings.find_one({"_id": "global"})
        if not doc:
            return default
        return doc.get(key, default if default is not None else DEFAULT_SETTINGS.get(key))

    # ── Persistent Admin Management ───────────────────────────────────────────

    async def get_extra_admins(self) -> List[int]:
        return await self.get_setting("extra_admins", [])

    async def add_extra_admin(self, user_id: int) -> bool:
        admins = await self.get_extra_admins()
        if user_id in admins:
            return False
        admins.append(user_id)
        await self.set_setting("extra_admins", admins)
        return True

    async def remove_extra_admin(self, user_id: int) -> bool:
        admins = await self.get_extra_admins()
        if user_id not in admins:
            return False
        admins.remove(user_id)
        await self.set_setting("extra_admins", admins)
        return True

    # ── RSS Feeds ─────────────────────────────────────────────────────────────

    async def add_rss(self, url: str, name: str, added_by: int) -> bool:
        try:
            await self.rss.insert_one({
                "_id": url,
                "url": url,
                "name": name,
                "added_by": added_by,
                "added_at": _now(),
                "active": True,
            })
            return True
        except Exception:
            return False

    async def remove_rss(self, url: str) -> bool:
        res = await self.rss.delete_one({"_id": url})
        return res.deleted_count > 0

    async def get_all_rss(self) -> List[Dict]:
        return await self.rss.find().to_list(None)

    async def get_active_rss(self) -> List[Dict]:
        return await self.rss.find({"active": True}).to_list(None)

    async def rss_exists(self, url: str) -> bool:
        return bool(await self.rss.find_one({"_id": url}))

    async def rss_count(self) -> int:
        return await self.rss.count_documents({})

    # ── Channels ──────────────────────────────────────────────────────────────

    async def add_channel(
        self,
        channel_id: int,
        title: str,
        username: Optional[str],
        added_by: int,
    ) -> bool:
        try:
            await self.channels.insert_one({
                "_id": channel_id,
                "channel_id": channel_id,
                "title": title,
                "username": username,
                "added_by": added_by,
                "added_at": _now(),
            })
            return True
        except Exception:
            return False

    async def remove_channel(self, channel_id: int) -> bool:
        res = await self.channels.delete_one({"_id": channel_id})
        return res.deleted_count > 0

    async def get_all_channels(self) -> List[Dict]:
        return await self.channels.find().to_list(None)

    async def channel_exists(self, channel_id: int) -> bool:
        return bool(await self.channels.find_one({"_id": channel_id}))

    async def channel_count(self) -> int:
        return await self.channels.count_documents({})

    # ── Seen / Dedup ──────────────────────────────────────────────────────────

    async def is_seen(self, guid: str) -> bool:
        return bool(await self.seen.find_one({"_id": guid}))

    async def mark_seen(self, guid: str):
        try:
            await self.seen.insert_one({"_id": guid, "published_at": _now()})
        except Exception:
            pass

    async def clear_seen(self) -> int:
        res = await self.seen.delete_many({})
        return res.deleted_count

    # ── Stats ─────────────────────────────────────────────────────────────────

    async def increment_published(self, count: int = 1):
        await self.stats.update_one(
            {"_id": "global"},
            {"$inc": {"published": count}, "$set": {"last_published": _now()}},
            upsert=True,
        )

    async def get_stats(self) -> Dict[str, Any]:
        doc = await self.stats.find_one({"_id": "global"}) or {}
        return {
            "published": doc.get("published", 0),
            "last_published": doc.get("last_published"),
        }

    async def reset_stats(self):
        await self.stats.update_one(
            {"_id": "global"},
            {"$set": {"published": 0, "last_published": None}},
            upsert=True,
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)
