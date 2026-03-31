"""
Config — loaded entirely from environment variables.
No credentials are hardcoded anywhere.

ADMINS list:
  ADMINS[0]  = Owner — set via OWNER_ID env var. Cannot be removed.
  ADMINS[1+] = Extra admins — from EXTRA_ADMINS env or added via /add_admin.

All permission checks use ADMINS. OWNER_ID is read once from env to seed
ADMINS[0] and is not stored as a separate attribute.
"""

import os
import sys
import logging
from typing import List

logger = logging.getLogger(__name__)


class Config:
    def __init__(self):
        # ── Required ──────────────────────────────────────────────────────────
        self.BOT_TOKEN: str = _require("BOT_TOKEN")
        self.API_ID: int    = int(_require("API_ID"))
        self.API_HASH: str  = _require("API_HASH")
        self.MONGO_URI: str = _require("MONGO_URI")

        # Owner is always ADMINS[0]. OWNER_ID is only used here to seed the list.
        owner_id = int(_require("OWNER_ID"))

        # ── Optional ──────────────────────────────────────────────────────────
        self.DB_NAME: str        = os.getenv("DB_NAME", "anime_news_bot")
        self.POLL_INTERVAL: int  = int(os.getenv("POLL_INTERVAL", "300"))
        self.MAX_RSS: int        = int(os.getenv("MAX_RSS", "25"))
        self.MAX_CHANNELS: int   = int(os.getenv("MAX_CHANNELS", "10"))

        # ── Webhook ───────────────────────────────────────────────────────────
        self.WEBHOOK: bool      = os.getenv("WEBHOOK", "false").lower() == "true"
        self.WEBHOOK_HOST: str  = os.getenv("WEBHOOK_HOST", "0.0.0.0")
        self.WEBHOOK_PORT: int  = int(os.getenv("PORT", "8080"))

        # ── Post format ───────────────────────────────────────────────────────
        self.DISABLE_WEB_PREVIEW: bool = os.getenv("DISABLE_WEB_PREVIEW", "false").lower() == "true"
        self.POST_FOOTER: str          = os.getenv("POST_FOOTER", "")

        # ── ADMINS list — owner is always index 0 ─────────────────────────────
        extra = _parse_ids(os.getenv("EXTRA_ADMINS", ""))
        self.ADMINS: List[int] = [owner_id] + [uid for uid in extra if uid != owner_id]

        logger.info(f"✅ Config loaded. Owner (Admins[0]): {self.ADMINS[0]} | All admins: {self.ADMINS}")

    # ── Permission helpers ────────────────────────────────────────────────────

    def is_owner(self, user_id: int) -> bool:
        """True only for ADMINS[0]."""
        return user_id == self.ADMINS[0]

    def is_admin(self, user_id: int) -> bool:
        """True for owner and all extra admins."""
        return user_id in self.ADMINS

    def add_admin(self, user_id: int) -> bool:
        """Add an admin to the live list. Owner cannot be re-added."""
        if user_id not in self.ADMINS:
            self.ADMINS.append(user_id)
            return True
        return False

    def remove_admin(self, user_id: int) -> bool:
        """Remove an admin. ADMINS[0] (owner) can never be removed."""
        if user_id == self.ADMINS[0]:
            return False
        if user_id in self.ADMINS:
            self.ADMINS.remove(user_id)
            return True
        return False

    # ── Live setting updaters (called by /set_* handlers) ────────────────────

    def update_poll_interval(self, seconds: int):
        self.POLL_INTERVAL = max(30, seconds)

    def update_max_rss(self, n: int):
        self.MAX_RSS = max(1, n)

    def update_max_channels(self, n: int):
        self.MAX_CHANNELS = max(1, n)

    def set_post_footer(self, text: str):
        self.POST_FOOTER = text.strip()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        logger.critical(f"❌ Required env var '{key}' is not set. Exiting.")
        sys.exit(1)
    return val


def _parse_ids(raw: str) -> List[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]
