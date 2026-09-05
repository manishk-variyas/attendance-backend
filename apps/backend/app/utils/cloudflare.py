"""Helpers for reading real client signals from request headers.

The frontend (browser) sets `X-User-Public-IP` to the user's real public IP.
Cloudflare sets `CF-Connecting-IP` if traffic is fronted by Cloudflare.
nginx sets `X-Real-IP` and `X-Forwarded-For` based on its configuration.

We combine these to build a stable device fingerprint for new-device
detection: any of these signals changing between two logins means a new
device (or a new network).
"""
import hashlib

from fastapi import Request

from app.middleware.logging import _get_client_ip


def get_country(request: Request) -> str | None:
    """Read the country code (e.g. 'IN', 'US') from Cloudflare's header.

    Returns None if the header is absent (e.g. traffic not via Cloudflare).
    """
    country = request.headers.get("cf-ipcountry", "").strip().upper()
    if country and country != "XX":
        return country
    return None


def get_user_agent(request: Request) -> str:
    """Read the User-Agent header. Returns '' if absent."""
    return request.headers.get("user-agent", "").strip()


def get_accept_language(request: Request) -> str:
    """Read the Accept-Language header. Returns '' if absent."""
    return request.headers.get("accept-language", "").strip()


def compute_fingerprint(request: Request) -> str:
    """Compute a stable device fingerprint hash from request signals.

    Inputs (in priority order, all normalized):
      - Real client IP (X-User-Public-IP > CF-Connecting-IP > X-Real-IP > X-Forwarded-For > client.host)
      - Country (CF-IPCountry, optional)
      - User-Agent
      - Accept-Language

    Output: lowercase hex SHA-256 of the canonical inputs.
    Same device + same network = same hash; new device or new network = new hash.
    """
    client_ip = _get_client_ip(request)
    country = get_country(request) or ""
    ua = get_user_agent(request)
    lang = get_accept_language(request)
    raw = f"{client_ip}|{country}|{ua}|{lang}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
