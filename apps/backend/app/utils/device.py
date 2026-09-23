"""Device-stable fingerprinting for new-device login detection.

Unlike IP/country-based signals (which change with networks and need
Cloudflare), a device fingerprint must survive network changes and browser
updates. We hash only normalized device traits:

  browser family (firefox/chrome/safari/edge/...) + OS platform
  (+ mobile flag) + accept-language

Version numbers are stripped, IP and country are EXCLUDED (they belong in
the email as context, never in identity).
"""

import hashlib
import ipaddress
import re

from fastapi import Request

from app.middleware.logging import _get_client_ip

_MOBILE_RE = re.compile(r"mobile|android|iphone|ipad", re.I)

_BROWSER_NAMES = {
    "firefox": "Firefox",
    "chrome": "Chrome",
    "safari": "Safari",
    "edge": "Edge",
    "opera": "Opera",
    "chromium": "Chromium",
    "unknown": "Unknown browser",
}

_OS_NAMES = {
    "windows": "Windows",
    "macos": "macOS",
    "linux": "Linux",
    "android": "Android",
    "ios": "iOS",
    "unknown": "Unknown device",
}


def is_public_ip(value: str) -> bool:
    """True for routable public IPs; False for private/loopback/reserved/garbage."""
    try:
        ip = ipaddress.ip_address(value)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast)
    except ValueError:
        return False


def display_ip(request: Request) -> str:
    """Best-effort public IP for display/forensics (never security decisions).

    Uses the forwarded chain our own nginx sets. A direct-to-backend client
    can forge these headers, so rate limiting/blocks must keep using the TCP
    peer (see _get_security_ip). Falls back to TCP peer (may be internal).
    """
    headers = request.headers
    for name in ("x-user-public-ip", "x-real-ip"):
        v = (headers.get(name) or "").strip()
        if v:
            return v.split(",")[0].strip()
    fwd = (headers.get("x-forwarded-for") or "").strip()
    if fwd:
        # Our nginx appends; prefer the leftmost public hop, else first hop.
        parts = [p.strip() for p in fwd.split(",") if p.strip()]
        for part in parts:
            if is_public_ip(part):
                return part
        if parts:
            return parts[0]
    return _get_client_ip(request)


def normalize_ua(ua: str) -> tuple[str, str, bool]:
    """Return (browser_family, os_platform, is_mobile) with versions stripped."""
    u = ua or ""
    low = u.lower()
    if "firefox" in low and "seamonkey" not in low:
        family = "firefox"
    elif "edg" in low:
        family = "edge"
    elif "opr" in low or "opera" in low:
        family = "opera"
    elif "chrome" in low and "chromium" not in low:
        family = "chrome"
    elif "chromium" in low:
        family = "chromium"
    elif "safari" in low:
        family = "safari"
    else:
        family = "unknown"
    if "android" in low:
        platform = "android"
    elif "iphone" in low or "ipad" in low:
        platform = "ios"
    elif "windows" in low:
        platform = "windows"
    elif "macintosh" in low or "mac os" in low:
        platform = "macos"
    elif "linux" in low:
        platform = "linux"
    else:
        platform = "unknown"
    return family, platform, bool(_MOBILE_RE.search(u))


def _primary_language(header_value: str) -> str:
    """Reduce Accept-Language to its primary tag (`en-US,en;q=0.9` → `en`).

    Quality values and region subtags drift between requests/clients; only
    the primary language carries device identity.
    """
    first = (header_value or "").split(",")[0].strip().lower()
    return re.split(r"[-_]", first)[0] if first else ""


def compute_device_fingerprint(request: Request) -> str:
    """Stable device hash: browser family + OS + mobile flag + language."""
    ua = (request.headers.get("user-agent") or "").strip()
    lang = _primary_language(request.headers.get("accept-language") or "")
    family, platform, mobile = normalize_ua(ua)
    raw = f"{family}|{platform}|{int(mobile)}|{lang}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def device_label(request: Request) -> str:
    """Human-readable device description for emails/logs: 'Safari · macOS'."""
    ua = (request.headers.get("user-agent") or "").strip()
    if not ua:
        return "Unknown device"
    family, platform, _mobile = normalize_ua(ua)
    if family == "unknown" and platform == "unknown":
        return "Unknown device"
    return f"{_BROWSER_NAMES.get(family, 'Browser')} · {_OS_NAMES.get(platform, 'Unknown device')}"
