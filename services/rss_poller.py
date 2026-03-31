"""
RSSPoller — background asyncio task.

Polls all ACTIVE registered feeds every POLL_INTERVAL seconds,
deduplicates via CosmicBotz.seen, formats and sends to all channels.

Production improvements:
 - Reads poll_interval from DB settings (live config changes apply on next cycle)
 - Per-feed consecutive error tracking with backoff logging
 - Rate-limit aware: 0.5s delay between channel sends
 - Graceful shutdown via stop() method
 - Reads post_footer and disable_web_preview from live DB settings
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

import aiohttp
import feedparser

from config import Config
from database import CosmicBotz

logger = logging.getLogger(__name__)

# ── Category emoji map ────────────────────────────────────────────────────────
EMOJI_MAP = {
    "anime":   "🎌",
    "manga":   "📖",
    "review":  "⭐",
    "trailer": "🎥",
    "episode": "📺",
    "release": "🗓",
    "game":    "🎮",
    "movie":   "🎬",
    "news":    "📰",
}


def _pick_emoji(tags: List[str], title: str) -> str:
    blob = " ".join(tags + [title]).lower()
    for kw, em in EMOJI_MAP.items():
        if kw in blob:
            return em
    return "📰"


def _clean_html(raw: str, max_len: int = 300) -> str:
    text = re.sub(r"<[^>]+>", "", raw).strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0] + "…"
    return text


def _fmt_date(parsed) -> Optional[str]:
    if not parsed:
        return None
    try:
        dt = datetime(*parsed[:6], tzinfo=timezone.utc)
        return dt.strftime("%d %b %Y · %H:%M UTC")
    except Exception:
        return None


def _guid(entry: Dict, feed_url: str) -> str:
    raw = entry.get("id") or entry.get("link") or entry.get("title") or ""
    return hashlib.sha1(f"{feed_url}:{raw}".encode()).hexdigest()


def _format(entry: Dict, feed_name: str, footer: str = "") -> str:
    title   = entry.get("title", "No Title").strip()
    link    = entry.get("link", "")
    summary = _clean_html(entry.get("summary", ""))
    tags    = [t.get("term", "") for t in entry.get("tags", [])]
    emoji   = _pick_emoji(tags, title)
    date    = _fmt_date(entry.get("published_parsed"))

    parts = [f"{emoji} **{title}**"]
    if summary:
        parts.append(f"\n{summary}")
    if date:
        parts.append(f"\n🕐 __{date}__")
    parts.append(f"\n📡 __{feed_name}__")
    if link:
        parts.append(f"\n[🔗 Read more]({link})")
    if footer:
        parts.append(f"\n\n{footer}")

    return "\n".join(parts)


# ── Poller ────────────────────────────────────────────────────────────────────

class RSSPoller:
    def __init__(self, client, db: CosmicBotz, cfg: Config):
        self._client  = client
        self._db      = db
        self._cfg     = cfg
        self._running = False
        self._task: Optional[asyncio.Task] = None
        # Track consecutive errors per feed URL for backoff logging
        self._feed_errors: Dict[str, int] = {}

    async def run(self):
        self._running = True
        # Read live interval from DB
        interval = await self._db.get_setting("poll_interval", self._cfg.POLL_INTERVAL)
        logger.info(f"📡 RSS Poller started — interval {interval}s")
        while self._running:
            try:
                await self._poll_all()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Poller loop error: {e}", exc_info=True)
            # Reload interval each cycle so live changes take effect
            interval = await self._db.get_setting("poll_interval", self._cfg.POLL_INTERVAL)
            await asyncio.sleep(interval)

    def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

    async def _poll_all(self):
        feeds    = await self._db.get_active_rss()
        channels = await self._db.get_all_channels()

        if not feeds or not channels:
            return

        channel_ids = [c["channel_id"] for c in channels]
        # Load live settings
        disable_preview = await self._db.get_setting("disable_web_preview", False)
        footer          = await self._db.get_setting("post_footer", "")

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            headers={"User-Agent": "AnimeNewsBot/2.0 PyroFork"},
        ) as session:
            for feed in feeds:
                await self._process_feed(feed, channel_ids, session, disable_preview, footer)

    async def _process_feed(
        self,
        feed: Dict,
        channel_ids: List[int],
        session: aiohttp.ClientSession,
        disable_preview: bool,
        footer: str,
    ):
        url  = feed["url"]
        name = feed.get("name", url)

        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    self._log_feed_error(url, f"HTTP {resp.status}")
                    return
                content = await resp.read()
            self._feed_errors[url] = 0  # reset on success
        except Exception as e:
            self._log_feed_error(url, str(e))
            return

        parsed  = feedparser.parse(content)
        entries = parsed.get("entries", [])
        new     = 0

        for entry in reversed(entries):  # oldest first
            guid = _guid(entry, url)
            if await self._db.is_seen(guid):
                continue

            text      = _format(entry, name, footer)
            published = False

            for ch_id in channel_ids:
                try:
                    await self._client.send_message(
                        ch_id,
                        text,
                        disable_web_page_preview=disable_preview,
                    )
                    published = True
                except Exception as e:
                    logger.warning(f"Send to {ch_id} failed: {e}")

            await self._db.mark_seen(guid)
            if published:
                new += 1
            await asyncio.sleep(0.5)  # respect Telegram rate limits

        if new:
            await self._db.increment_published(new)
            logger.info(f"📨 {new} new article(s) from '{name}'")

    def _log_feed_error(self, url: str, reason: str):
        count = self._feed_errors.get(url, 0) + 1
        self._feed_errors[url] = count
        # Only log every 1st, 3rd, 10th, then every 10
        if count in (1, 3, 10) or count % 10 == 0:
            logger.warning(f"Feed error [{url}] (×{count}): {reason}")
