"""
link_extractor.py — Detects and classifies Telegram & WhatsApp links.

Classification rules:
  TELEGRAM  → t.me/joinchat/... or t.me/+... (always group invites)
              t.me/username where the entity resolves to a Channel/Megagroup
  WHATSAPP  → chat.whatsapp.com/... (always group invites)

Ignored:
  - wa.me/... (individual WhatsApp contacts)
  - t.me/<numeric> (direct user IDs)
  - t.me/username where entity resolves to a User
"""

import re
from dataclasses import dataclass
from typing import Literal, Optional

LinkType = Literal["telegram", "whatsapp"]


@dataclass
class ExtractedLink:
    url: str
    link_type: LinkType
    is_invite: bool          # True if we already know it's a group invite (no API check needed)
    username: Optional[str]  # For public t.me/username links that need resolution


# Patterns that are definitively Telegram group/channel invite links
_TG_INVITE_RE = re.compile(
    r'(?:https?://)?(?:t(?:elegram)?\.me|telegram\.org)'
    r'/(?:joinchat/[a-zA-Z0-9_\-]+|\+[a-zA-Z0-9_\-]+)',
    re.IGNORECASE,
)

# Public t.me/username links — need to be resolved to know if group/channel or user
_TG_PUBLIC_RE = re.compile(
    r'(?:https?://)?(?:t(?:elegram)?\.me|telegram\.org)'
    r'/([a-zA-Z][a-zA-Z0-9_]{3,})',
    re.IGNORECASE,
)

# WhatsApp group invite links — chat.whatsapp.com is always a group
_WA_GROUP_RE = re.compile(
    r'(?:https?://)?chat\.whatsapp\.com/[a-zA-Z0-9_\-]+',
    re.IGNORECASE,
)

# Patterns to explicitly skip (user-specific, not groups)
_SKIP_RE = re.compile(
    r'(?:https?://)?wa\.me/\d+|'
    r'(?:https?://)?(?:t\.me|telegram\.me)/\d+',
    re.IGNORECASE,
)

# Known bot/service usernames that should not be collected
_KNOWN_BOTS = {"telegramtips", "telegram", "gif", "sticker", "pic", "bold"}


def extract_links(text: str) -> list[ExtractedLink]:
    """
    Extract all candidate links from a message body.
    Returns a list of ExtractedLink objects.
    Links that are obviously not groups (wa.me, numeric IDs) are silently skipped.
    """
    results: list[ExtractedLink] = []
    seen_urls: set[str] = set()

    # 1. WhatsApp group invite links
    for match in _WA_GROUP_RE.finditer(text):
        url = _normalise(match.group(0))
        if url not in seen_urls and not _SKIP_RE.search(url):
            seen_urls.add(url)
            results.append(ExtractedLink(url=url, link_type="whatsapp", is_invite=True, username=None))

    # 2. Telegram invite links (joinchat / +hash) — definitively group invites
    for match in _TG_INVITE_RE.finditer(text):
        url = _normalise(match.group(0))
        if url not in seen_urls:
            seen_urls.add(url)
            results.append(ExtractedLink(url=url, link_type="telegram", is_invite=True, username=None))

    # 3. Public Telegram links — need entity resolution
    for match in _TG_PUBLIC_RE.finditer(text):
        url = _normalise(match.group(0))
        username = match.group(1).lower()
        # Skip if already captured as invite link or explicitly skipped
        if url in seen_urls:
            continue
        if _SKIP_RE.search(url):
            continue
        if username in _KNOWN_BOTS:
            continue
        # Skip obviously bot-named usernames (end in 'bot')
        if username.endswith("bot"):
            continue
        seen_urls.add(url)
        results.append(ExtractedLink(url=url, link_type="telegram", is_invite=False, username=username))

    return results


def _normalise(url: str) -> str:
    """Ensure the URL starts with https:// and has no trailing slash."""
    url = url.strip().rstrip("/")
    if not url.startswith("http"):
        url = "https://" + url
    return url.lower()
