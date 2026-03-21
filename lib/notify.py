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


def send_telegram(text, title=None):
    """Send a message to all configured Telegram recipients."""
    token, chat_ids = _get_telegram_config()
    if not token or not chat_ids:
        print("  NOTIFY: Telegram not configured (missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_IDS)")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = _split_chunks(text)
    ok = True

    for chat_id in chat_ids:
        for i, chunk in enumerate(chunks):
            body = json.dumps({
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "Markdown",
            }).encode("utf-8")
            req = Request(url, method="POST", data=body)
            req.add_header("Content-Type", "application/json")
            try:
                with urlopen(req, timeout=15) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    if result.get("ok"):
                        if len(chunks) > 1:
                            print(f"  Sent chunk {i + 1}/{len(chunks)} to Telegram {chat_id}")
                        else:
                            print(f"  Sent to Telegram {chat_id}")
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
            return send_telegram(text, title=title)
    else:
        return send_telegram(text, title=title)
