"""
Anime News Bot — Production Edition
Render / Koyeb / Heroku / VPS ready.

Startup sequence:
  1. Load env vars (auto-reads .env if present)
  2. Init MongoDB + indexes
  3. Seed default feeds if DB is empty
  4. Sync persisted DB settings back into Config
  5. Start PyroFork client with auto-discovered handlers
  6. Launch background RSSPoller task
  7. Health-check HTTP server (if WEBHOOK=true)
"""

import asyncio
import logging

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from aiohttp import web
from pyrogram import Client, idle

from config import Config
from database import CosmicBotz
from services.rss_poller import RSSPoller

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ── Health-check server ───────────────────────────────────────────────────────

async def _health(request: web.Request) -> web.Response:
    return web.Response(text="OK")


async def _start_webhook(host: str, port: int) -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", _health)
    app.router.add_get("/health", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    logger.info(f"🌐 Health-check server running on {host}:{port}")
    return runner


# ── DB → Config sync ─────────────────────────────────────────────────────────

async def _sync_settings(db: CosmicBotz, cfg: Config):
    """
    Reload persisted settings from MongoDB into the live Config object.
    Ensures /set_* changes survive bot restarts.
    """
    s = await db.get_settings()
    cfg.POLL_INTERVAL       = s.get("poll_interval", cfg.POLL_INTERVAL)
    cfg.MAX_RSS             = s.get("max_rss", cfg.MAX_RSS)
    cfg.MAX_CHANNELS        = s.get("max_channels", cfg.MAX_CHANNELS)
    cfg.DISABLE_WEB_PREVIEW = s.get("disable_web_preview", False)
    cfg.POST_FOOTER         = s.get("post_footer", "")

    for uid in s.get("extra_admins", []):
        cfg.add_admin(uid)

    logger.info(
        f"⚙️  Settings synced — interval={cfg.POLL_INTERVAL}s "
        f"max_rss={cfg.MAX_RSS} max_channels={cfg.MAX_CHANNELS} "
        f"admins={cfg.ADMINS}"
    )


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    cfg = Config()

    db = CosmicBotz(cfg.MONGO_URI, cfg.DB_NAME)
    await db.init()
    await db.seed_defaults()
    await _sync_settings(db, cfg)

    app = Client(
        name="AnimeNewsBot",
        bot_token=cfg.BOT_TOKEN,
        api_id=cfg.API_ID,
        api_hash=cfg.API_HASH,
        plugins={"root": "handlers"},
    )
    app.db  = db
    app.cfg = cfg

    poller = RSSPoller(app, db, cfg)
    # Attach poller to client so /force_poll can reuse it
    app.poller = poller

    await app.start()
    logger.info(f"✅ Bot started. Owner: {cfg.ADMINS[0]} | Admins: {cfg.ADMINS}")

    poller.start()

    if cfg.WEBHOOK:
        logger.info("🚀 WEBHOOK mode")
        runner = await _start_webhook(cfg.WEBHOOK_HOST, cfg.WEBHOOK_PORT)
        await idle()
        poller.stop()
        await runner.cleanup()
    else:
        logger.info("🚀 POLLING mode")
        await idle()
        poller.stop()

    await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
