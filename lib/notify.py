#!/usr/bin/env python3
"""
Shared notification module — routes messages to Telegram or ntfy.

Sensitive data (financial figures, account balances) → ntfy (self-hosted)
Non-sensitive data (community signals, status updates) → Telegram

Usage:
    from usecases.notify import notify

    notify("Your briefing text", title="Daily Finance", topic="finance-daily", sensitive=True)
    notify("Community update", title="6529 Brief", topic="community", sensitive=False)

Environment:
    TELEGRAM_BOT_TOKEN  - Telegram Bot API token
    TELEGRAM_CHAT_IDS   - Comma-separated Telegram chat IDs
    NTFY_URL            - ntfy server URL (e.g., http://localhost:2586)
    NTFY_TOKEN          - ntfy auth token
"""

import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from common import load_env


# ── Config loading ───────────────────────────────────────────────

_ENV_FILES = [
    Path.home() / "slos" / "config" / "finance.env",
    Path.home() / "slos" / "config" / "community.env",
    # Legacy fallbacks
    Path.home() / ".config" / "sovereign-finance" / "simplefin.env",
    Path.home() / ".config" / "sovereign-community" / "community.env",
]

load_env()


def _get_telegram_config():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_ids = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_IDS", "").split(",") if c.strip()]
    return token, chat_ids


def _get_ntfy_config():
    url = os.environ.get("NTFY_URL", "")
    token = os.environ.get("NTFY_TOKEN", "")
    return url, token


# ── Telegram ─────────────────────────────────────────────────────


def _split_chunks(text, max_len=4000):
    """Split text into chunks that fit within Telegram's 4096-char limit."""
    if len(text) <= 4096:
        return [text]
    chunks = []
    chunk = ""
    for line in text.split("\n"):
        if len(chunk) + len(line) + 1 > max_len:
            chunks.append(chunk)
            chunk = line
        else:
            chunk += "\n" + line if chunk else line
    if chunk:
        chunks.append(chunk)
    return chunks


def _forum_enabled():
    return os.environ.get("TELEGRAM_FORUM_ENABLED", "").lower() == "true"


def _get_forum_config():
    """Get forum chat ID and topic thread_id map when forum mode is active."""
    if not _forum_enabled():
        return None, {}
    chat_id = os.environ.get("TELEGRAM_FORUM_CHAT_ID", "")
    if not chat_id:
        return None, {}
    # Lazy import — topic_manager lives in telegram/ dir
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "telegram"))
        from topic_manager import load_topic_map
        return chat_id, load_topic_map()
    except ImportError:
        return chat_id, {}


def send_telegram(text, title=None, parse_mode="Markdown", reply_markup=None, topic_key=None):
    """Send a message to all configured Telegram recipients.

    Args:
        reply_markup: Optional dict for inline keyboards, e.g.
            {"inline_keyboard": [[{"text": "OK", "callback_data": "ok"}]]}
        topic_key: Forum topic to send to (e.g. "finance", "venture").
            Only used when TELEGRAM_FORUM_ENABLED=true.
    """
    token, chat_ids = _get_telegram_config()
    if not token:
        print("  NOTIFY: Telegram not configured (missing TELEGRAM_BOT_TOKEN)")
        return False

    # Forum mode: route to supergroup topic instead of private chat
    forum_chat_id, topic_map = _get_forum_config()
    if forum_chat_id:
        chat_ids = [forum_chat_id]

    if not chat_ids:
        print("  NOTIFY: No Telegram recipients configured")
        return False

    thread_id = topic_map.get(topic_key) if topic_key and forum_chat_id else None

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = _split_chunks(text)
    ok = True

    for chat_id in chat_ids:
        for i, chunk in enumerate(chunks):
            payload = {
                "chat_id": chat_id,
                "text": chunk,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode
            if thread_id:
                payload["message_thread_id"] = thread_id
            # Only attach keyboard to last chunk
            if reply_markup and i == len(chunks) - 1:
                payload["reply_markup"] = reply_markup
            body = json.dumps(payload).encode("utf-8")
            req = Request(url, method="POST", data=body)
            req.add_header("Content-Type", "application/json")
            try:
                with urlopen(req, timeout=15) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    if result.get("ok"):
                        topic_info = f" [topic:{topic_key}]" if topic_key and thread_id else ""
                        if len(chunks) > 1:
                            print(f"  Sent chunk {i + 1}/{len(chunks)} to Telegram {chat_id}{topic_info}")
                        else:
                            print(f"  Sent to Telegram {chat_id}{topic_info}")
                    else:
                        print(f"  Telegram failed for {chat_id}: {result.get('description')}")
                        ok = False
            except (HTTPError, URLError) as e:
                print(f"  Telegram error for {chat_id}: {e}")
                ok = False
    return ok


def telegram_get_updates():
    """Get recent messages sent to the bot (for discovering chat IDs)."""
    token, _ = _get_telegram_config()
    if not token:
        print("  NOTIFY: TELEGRAM_BOT_TOKEN not configured")
        return {}
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    body = json.dumps({"limit": 10}).encode("utf-8")
    req = Request(url, method="POST", data=body)
    req.add_header("Content-Type", "application/json")
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── ntfy ─────────────────────────────────────────────────────────


def send_ntfy(text, title="Notification", topic="general", priority="default",
              tags=None, click_url=None, markdown=True):
    """Send a notification via self-hosted ntfy.

    Args:
        priority: min, low, default, high, max/urgent
        tags: comma-separated emoji shortcodes (e.g. "money_with_wings,warning")
        click_url: URL to open when notification is tapped
        markdown: enable markdown rendering (bold, italic, links)
    """
    ntfy_url, ntfy_token = _get_ntfy_config()
    if not ntfy_url:
        print("  NOTIFY: ntfy not configured (missing NTFY_URL)")
        return False

    url = f"{ntfy_url.rstrip('/')}/{topic}"
    req = Request(url, data=text.encode("utf-8"), method="POST")
    req.add_header("Title", title)
    req.add_header("Priority", priority)
    if markdown:
        req.add_header("Markdown", "yes")
    if tags:
        req.add_header("Tags", tags)
    if click_url:
        req.add_header("Click", click_url)
    if ntfy_token:
        req.add_header("Authorization", f"Bearer {ntfy_token}")

    try:
        with urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                print(f"  Sent to ntfy topic '{topic}'")
                return True
            else:
                print(f"  ntfy error: HTTP {resp.status}")
                return False
    except (HTTPError, URLError) as e:
        print(f"  ntfy error: {e}")
        return False


# ── Router ───────────────────────────────────────────────────────


FINANCIAL_PATTERNS = [
    r'\$[\d,]+\.?\d*',          # Dollar amounts
    r'balance[:\s]+\$',         # Balance figures
    r'account\s*#?\s*\d{4}',    # Account numbers
    r'SSN|social security',     # SSN references
    r'routing\s*number',        # Routing numbers
]

import re as _re
_FINANCIAL_RE = [_re.compile(p, _re.IGNORECASE) for p in FINANCIAL_PATTERNS]


def _contains_financial_data(text: str) -> bool:
    """Detect financial data in notification text."""
    return any(p.search(text) for p in _FINANCIAL_RE)


# Map ntfy topic slugs → Telegram forum topic keys
# Used by notify() to auto-route to the correct forum topic
NTFY_TO_TELEGRAM_TOPIC = {
    "finance-daily": "finance",
    "finance-weekly": "finance",
    "finance-investments": "finance",
    "cost-ops": "finance",
    "venture": "venture",
    "community": "community",
    "exit-watch": "system",
    "health": "system",
    "digest": "general",
    "email-digest": "email",
    "quality-gate": "approvals",
    "sentinel-alert": "approvals",
    "content-approval": "approvals",
    "general": "general",
}


def notify(text, title="Notification", topic="general", sensitive=False,
           priority="default", tags=None, click_url=None):
    """Route notification based on sensitivity. Financial content → ntfy only.

    Sensitive data (financial) → ntfy (self-hosted, encrypted, no 3rd party)
    Non-sensitive data → Telegram (convenient, good UX)

    SECURITY: If content contains financial data patterns ($, account numbers),
    it is ALWAYS routed to ntfy regardless of the sensitive flag. If ntfy is
    down, financial content is BLOCKED (not sent to Telegram).
    """
    # Guard 4: Detect financial data in text — force ntfy routing
    has_financial = _contains_financial_data(text)
    if has_financial and not sensitive:
        print("  GUARD: Financial data detected in non-sensitive notification — upgrading to ntfy")
        sensitive = True

    # Derive Telegram forum topic from ntfy topic slug
    telegram_topic = NTFY_TO_TELEGRAM_TOPIC.get(topic, "general")

    if sensitive:
        ntfy_url, _ = _get_ntfy_config()
        if ntfy_url:
            return send_ntfy(text, title=title, topic=topic,
                             priority=priority, tags=tags, click_url=click_url)
        else:
            if has_financial:
                # BLOCKED: financial content cannot fall back to Telegram
                print("  BLOCKED: Financial data cannot route to Telegram (ntfy unavailable)")
                return False
            print("  NOTIFY: ntfy not configured, falling back to Telegram for sensitive data")
            return send_telegram(text, title=title, topic_key=telegram_topic)
    else:
        return send_telegram(text, title=title, topic_key=telegram_topic)
