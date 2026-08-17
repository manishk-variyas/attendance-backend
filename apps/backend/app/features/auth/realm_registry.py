"""
Realm registry — resolves which Keycloak realm a request belongs to.

Keeps the existing single-realm behavior (`attendance-app`) untouched:
`get_realm_config(None)` always returns the default realm built from the
current environment settings. Additional realms are opt-in via the
`EXTRA_REALMS` env var (JSON array); if unset, nothing changes.
"""
import json
import logging
from dataclasses import dataclass

from fastapi import HTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RealmConfig:
    """Connection details for one Keycloak realm."""

    name: str  # logical name exposed to API clients ("" → default realm)
    keycloak_url: str
    realm: str  # actual Keycloak realm name
    client_id: str
    client_secret: str

    @property
    def token_url(self) -> str:
        return f"{self.keycloak_url}/realms/{self.realm}/protocol/openid-connect/token"

    @property
    def logout_url(self) -> str:
        return f"{self.keycloak_url}/realms/{self.realm}/protocol/openid-connect/logout"

    @property
    def jwks_url(self) -> str:
        return f"{self.keycloak_url}/realms/{self.realm}/protocol/openid-connect/certs"

    @property
    def issuer(self) -> str:
        return f"{self.keycloak_url}/realms/{self.realm}"

    @property
    def admin_base(self) -> str:
        return f"{self.keycloak_url}/admin/realms/{self.realm}"


def _load_extra_realms() -> dict:
    """Parse EXTRA_REALMS env JSON into {name: RealmConfig}. Empty → {}."""
    if not settings.EXTRA_REALMS:
        return {}
    try:
        raw = json.loads(settings.EXTRA_REALMS)
    except json.JSONDecodeError:
        logger.error("EXTRA_REALMS is not valid JSON; ignoring additional realms")
        return {}
    out = {}
    for item in raw:
        name = item.get("name")
        if not name:
            logger.warning("Skipping EXTRA_REALMS entry without a name")
            continue
        out[name] = RealmConfig(
            name=name,
            keycloak_url=item.get("keycloak_url", settings.KEYCLOAK_URL),
            realm=item.get("realm", name),
            client_id=item.get("client_id", settings.KEYCLOAK_CLIENT_ID),
            client_secret=item.get("client_secret", ""),
        )
    return out


_EXTRA_REALMS = _load_extra_realms()

DEFAULT_REALM_NAME = settings.REALM


def get_realm_config(name: str | None = None) -> RealmConfig:
    """
    Resolve a realm by its logical name.

    - `None`/empty/default-realm-name → the default realm (current behavior,
      built from the existing environment settings — byte-for-byte the same
      URLs the app uses today).
    - Otherwise → look it up in EXTRA_REALMS.
    - Unknown name → HTTPException(400).
    """
    if not name:
        name = DEFAULT_REALM_NAME

    if name == DEFAULT_REALM_NAME:
        return RealmConfig(
            name=DEFAULT_REALM_NAME,
            keycloak_url=settings.KEYCLOAK_URL,
            realm=settings.REALM,
            client_id=settings.KEYCLOAK_CLIENT_ID,
            client_secret=settings.KEYCLOAK_CLIENT_SECRET,
        )

    cfg = _EXTRA_REALMS.get(name)
    if cfg is None:
        raise HTTPException(status_code=400, detail=f"Unknown realm: {name}")
    return cfg


def token_issuer_matches_realm(issuer: str, cfg: RealmConfig) -> bool:
    """
    True if a token's `iss` belongs to this realm, compared by realm-path
    suffix so it's agnostic to the Keycloak frontend URL
    (internal `keycloak:8080` vs public `localhost:8080`).
    """
    if not issuer:
        return False
    return issuer.rstrip("/").endswith(f"/realms/{cfg.realm}")