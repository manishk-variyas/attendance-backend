"""Resolve the real client IP for security decisions (rate limiting, IP blocks).

The API only listens on the internal Docker network and is reachable solely
through nginx (no published port), so nginx-set proxy headers cannot be forged
by clients. We therefore read ``X-Real-IP`` (nginx sets it to ``$remote_addr``)
and fall back to the leftmost ``X-Forwarded-For`` hop, then the TCP peer.

Do NOT use client-supplied headers here (e.g. ``X-User-Public-IP``) — they are
spoofable and would let a caller evade rate limits / IP blocks.
"""
from fastapi import Request


def security_client_ip(request: Request) -> str:
    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "-"
