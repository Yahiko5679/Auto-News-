"""
Admin Settings Panel — owner-only commands to configure the bot live.
All settings are persisted in MongoDB and survive restarts.

Commands:
  /settings           — View all current settings  (admin)
  /set_interval <s>   — Set poll interval in seconds, min 30  (owner)
  /set_max_rss <n>    — Set RSS feed limit  (owner)
  /set_max_channels   — Set channel limit  (owner)
  /toggle_preview     — Toggle web link preview on/off  (owner)
  /set_footer <text>  — Set a footer line on every post  (owner)
  /clear_footer       — Remove the footer  (owner)
  /add_admin <id>     — Add an admin, persisted in DB  (owner)
  /rem_admin <id>     — Remove an admin  (owner)
  /list_admins        — List all current admins  (admin)
  /clear_seen         — Wipe dedup cache  (owner)
  /reset_stats        — Reset publish counter  (owner)
"""

import logging

from pyrogram import Client, filters
from pyrogram.types import Message

from utils.auth import admin_only, owner_only

logger = logging.getLogger(__name__)


# ── /settings — overview ──────────────────────────────────────────────────────

@Client.on_message(filters.command("settings"))
@admin_only
async def cmd_settings(client: Client, msg: Message):
    db  = client.db
    cfg = client.cfg
    s   = await db.get_settings()

    preview_status = "🔴 Off" if s["disable_web_preview"] else "🟢 On"
    footer_preview = f"`{s['post_footer']}`" if s["post_footer"] else "_none_"
    admins_list    = ", ".join(f"`{uid}`" for uid in cfg.ADMINS)

    await msg.reply(
        f"⚙️ **Bot Settings**\n\n"
        f"**‹ Polling ›**\n"
        f"🔄 Poll Interval: `{s['poll_interval']}s`\n\n"
        f"**‹ Limits ›**\n"
        f"📡 Max RSS Feeds: `{s['max_rss']}`\n"
        f"📢 Max Channels: `{s['max_channels']}`\n\n"
        f"**‹ Post Format ›**\n"
        f"🔗 Web Preview: {preview_status}\n"
        f"📝 Footer: {footer_preview}\n\n"
        f"**‹ Admins ›**\n"
        f"👑 Owner: `{cfg.ADMINS[0]}`\n"
        f"🛡 All Admins: {admins_list}\n\n"
        f"_Type /help for full command list._"
    )


# ── Poll interval ─────────────────────────────────────────────────────────────

@Client.on_message(filters.command("set_interval"))
@owner_only
async def cmd_set_interval(client: Client, msg: Message):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await msg.reply("⚠️ **Usage:** `/set_interval <seconds>` (min 30)\n\nExample: `/set_interval 180`")
        return

    val = max(30, int(parts[1].strip()))
    await client.db.set_setting("poll_interval", val)
    client.cfg.update_poll_interval(val)
    await msg.reply(f"✅ Poll interval set to **{val}s**.\nTakes effect after the current cycle.")


# ── Max RSS ───────────────────────────────────────────────────────────────────

@Client.on_message(filters.command("set_max_rss"))
@owner_only
async def cmd_set_max_rss(client: Client, msg: Message):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await msg.reply("⚠️ **Usage:** `/set_max_rss <number>`\n\nExample: `/set_max_rss 50`")
        return

    val = max(1, int(parts[1].strip()))
    await client.db.set_setting("max_rss", val)
    client.cfg.update_max_rss(val)
    await msg.reply(f"✅ Max RSS feeds set to **{val}**.")


# ── Max Channels ──────────────────────────────────────────────────────────────

@Client.on_message(filters.command("set_max_channels"))
@owner_only
async def cmd_set_max_channels(client: Client, msg: Message):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await msg.reply("⚠️ **Usage:** `/set_max_channels <number>`\n\nExample: `/set_max_channels 20`")
        return

    val = max(1, int(parts[1].strip()))
    await client.db.set_setting("max_channels", val)
    client.cfg.update_max_channels(val)
    await msg.reply(f"✅ Max channels set to **{val}**.")


# ── Web Preview Toggle ────────────────────────────────────────────────────────

@Client.on_message(filters.command("toggle_preview"))
@owner_only
async def cmd_toggle_preview(client: Client, msg: Message):
    current = await client.db.get_setting("disable_web_preview", False)
    new_val = not current
    await client.db.set_setting("disable_web_preview", new_val)
    client.cfg.DISABLE_WEB_PREVIEW = new_val
    status = "🔴 **disabled**" if new_val else "🟢 **enabled**"
    await msg.reply(f"Web preview is now {status} for all published posts.")


# ── Post Footer ───────────────────────────────────────────────────────────────

@Client.on_message(filters.command("set_footer"))
@owner_only
async def cmd_set_footer(client: Client, msg: Message):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await msg.reply("⚠️ **Usage:** `/set_footer <text>`\n\nExample: `/set_footer 📢 @MyAnimeChannel`")
        return

    footer = parts[1].strip()
    await client.db.set_setting("post_footer", footer)
    client.cfg.set_post_footer(footer)
    await msg.reply(f"✅ Footer set:\n{footer}")


@Client.on_message(filters.command("clear_footer"))
@owner_only
async def cmd_clear_footer(client: Client, msg: Message):
    await client.db.set_setting("post_footer", "")
    client.cfg.set_post_footer("")
    await msg.reply("✅ Footer cleared.")


# ── Admin Management ──────────────────────────────────────────────────────────

@Client.on_message(filters.command("add_admin"))
@owner_only
async def cmd_add_admin(client: Client, msg: Message):
    cfg   = client.cfg
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().lstrip("-").isdigit():
        await msg.reply("⚠️ **Usage:** `/add_admin <user_id>`\n\nExample: `/add_admin 123456789`")
        return

    uid = int(parts[1].strip())
    if uid == cfg.ADMINS[0]:
        await msg.reply("ℹ️ That's the owner — already has full access.")
        return

    added_db  = await client.db.add_extra_admin(uid)
    added_cfg = cfg.add_admin(uid)

    if added_db or added_cfg:
        await msg.reply(f"✅ Admin added: `{uid}`")
    else:
        await msg.reply(f"ℹ️ `{uid}` is already an admin.")


@Client.on_message(filters.command("rem_admin"))
@owner_only
async def cmd_rem_admin(client: Client, msg: Message):
    cfg   = client.cfg
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().lstrip("-").isdigit():
        await msg.reply("⚠️ **Usage:** `/rem_admin <user_id>`")
        return

    uid = int(parts[1].strip())
    if uid == cfg.ADMINS[0]:
        await msg.reply("🚫 Cannot remove the owner.")
        return

    rem_db  = await client.db.remove_extra_admin(uid)
    rem_cfg = cfg.remove_admin(uid)

    if rem_db or rem_cfg:
        await msg.reply(f"🗑 Admin removed: `{uid}`")
    else:
        await msg.reply(f"⚠️ `{uid}` is not an admin.")


@Client.on_message(filters.command("list_admins"))
@admin_only
async def cmd_list_admins(client: Client, msg: Message):
    cfg   = client.cfg
    lines = [f"🛡 **Admins** `({len(cfg.ADMINS)})`\n"]
    for i, uid in enumerate(cfg.ADMINS):
        tag = "👑 Owner" if i == 0 else "🛡 Admin"
        lines.append(f"**{i+1}.** `{uid}` — {tag}")
    await msg.reply("\n".join(lines))


# ── Maintenance ───────────────────────────────────────────────────────────────

@Client.on_message(filters.command("clear_seen"))
@owner_only
async def cmd_clear_seen(client: Client, msg: Message):
    wait    = await msg.reply("⏳ Clearing dedup cache…")
    deleted = await client.db.clear_seen()
    await wait.edit(
        f"✅ Dedup cache cleared. Removed `{deleted}` entries.\n\n"
        f"⚠️ _Next poll may re-publish recent articles._"
    )


@Client.on_message(filters.command("reset_stats"))
@owner_only
async def cmd_reset_stats(client: Client, msg: Message):
    await client.db.reset_stats()
    await msg.reply("✅ Publish stats reset to zero.")
