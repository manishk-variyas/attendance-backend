import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from app.core.models import Base


class TrustedDevice(Base):
    """Device fingerprints a user has logged in from (WebAuthn-style memory).

    The fingerprint is device-stable (browser family + OS + language) and
    ignores network/IP, so the same laptop on a new network does NOT count
    as new. First sighting emails once, then auto-trusts; password resets
    wipe the table for the user.
    """

    __tablename__ = "trusted_device_hashes"
    __table_args__ = (
        UniqueConstraint("keycloak_user_id", "fingerprint", name="uq_trusted_device_user_fp"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    keycloak_user_id = Column(String(255), nullable=False, index=True)
    fingerprint = Column(String(64), nullable=False)
    device_label = Column(String(255), nullable=True)
    # Hash of the persistent device cookie (HttpOnly). Cookie match anchors
    # trust even when the fingerprint drifts (WebView vs browser, etc.).
    device_token_hash = Column(String(64), nullable=True, index=True)
    first_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    last_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class LoginAlertCooldown(Base):
    """One row per user: when the last new-device email was sent.

    Caps alert volume (max one email per window) so hash drift across
    clients can never turn into per-login nagging. Unknown devices are
    still logged + trusted; only the email is suppressed in-window.
    """

    __tablename__ = "login_alert_cooldown"

    keycloak_user_id = Column(String(255), primary_key=True)
    last_sent_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
