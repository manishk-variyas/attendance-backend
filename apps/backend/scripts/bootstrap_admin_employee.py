"""
bootstrap_admin_employee.py
────────────────────────────────────────────────────────────────────────────
One-off script to create the employee_master record for admin-vishal.

What it does:
  1. Logs into Keycloak with admin-vishal / vishaladmin@123
     → extracts keycloak_user_id (sub) and email from the JWT
  2. Hits Redmine API to look up the user by email
     → extracts redmine_user_id
  3. Directly upserts the employee_master row in Postgres

Run from inside the backend container or host with venv activated:
  python scripts/bootstrap_admin_employee.py

Or override defaults via env vars:
  KEYCLOAK_URL=http://localhost:8080 \
  REDMINE_URL=http://localhost:3000 \
  DATABASE_URL=postgresql://backend_user:backend_password@localhost:5432/attendance \
  python scripts/bootstrap_admin_employee.py
────────────────────────────────────────────────────────────────────────────
"""

import os
import sys
import httpx
import base64
import json
import uuid
from datetime import datetime, timezone
from sqlalchemy import create_engine, text

# ── Config (from env or defaults that match docker-compose) ──────────────────
KEYCLOAK_URL     = os.getenv("KEYCLOAK_URL",  "http://localhost:8080")
REALM            = os.getenv("REALM",         "attendance-app")
CLIENT_ID        = os.getenv("KEYCLOAK_CLIENT_ID",     "backend-client")
CLIENT_SECRET    = os.getenv("KEYCLOAK_CLIENT_SECRET", "best-practice-secret-12345")

REDMINE_URL      = os.getenv("REDMINE_URL",   "http://localhost:3000")
REDMINE_API_KEY  = os.getenv("REDMINE_API_KEY", "c2ffc5526ca3ec90d0359742c48aee46f6ec1688")

DATABASE_URL     = os.getenv(
    "DATABASE_URL",
    "postgresql://backend_user:backend_password@localhost:5432/attendance"
)

# Admin credentials to bootstrap
ADMIN_USERNAME = "admin-vishal"
ADMIN_PASSWORD = "vishaladmin@123"

# Employee fields (edit if needed)
FIRST_NAME   = "Vishal"
LAST_NAME    = "Admin"
DESIGNATION  = "System Administrator"
EMPLOYEE_ID  = "EMP-ADMIN-001"


# ── Helpers ──────────────────────────────────────────────────────────────────

def decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload without verifying signature (we trust Keycloak here)."""
    parts = token.split(".")
    if len(parts) < 2:
        raise ValueError("Invalid JWT token")
    payload_b64 = parts[1]
    # Add padding if needed
    payload_b64 += "=" * (4 - len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))


def step(msg: str):
    print(f"\n{'─'*60}\n▶  {msg}")


def ok(msg: str):
    print(f"   ✅  {msg}")


def warn(msg: str):
    print(f"   ⚠️   {msg}")


def fail(msg: str):
    print(f"   ❌  {msg}")
    sys.exit(1)


# ── Step 1: Login to Keycloak ─────────────────────────────────────────────────

step(f"Logging into Keycloak as '{ADMIN_USERNAME}'")

token_url = f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/token"
resp = httpx.post(
    token_url,
    data={
        "grant_type":    "password",
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "username":      ADMIN_USERNAME,
        "password":      ADMIN_PASSWORD,
        "scope":         "openid profile email",
    },
    headers={"Content-Type": "application/x-www-form-urlencoded"},
)

if resp.status_code != 200:
    fail(f"Keycloak login failed [{resp.status_code}]: {resp.text}")

tokens = resp.json()
access_token = tokens["access_token"]
payload      = decode_jwt_payload(access_token)

keycloak_user_id = payload.get("sub")
email            = payload.get("email")
preferred_name   = payload.get("preferred_username", ADMIN_USERNAME)

ok(f"keycloak_user_id : {keycloak_user_id}")
ok(f"email            : {email}")
ok(f"preferred_username: {preferred_name}")

if not keycloak_user_id or not email:
    fail("Could not extract 'sub' or 'email' from token payload.")


# ── Step 2: Look up Redmine user by email ────────────────────────────────────

step(f"Looking up Redmine user by email '{email}'")

redmine_resp = httpx.get(
    f"{REDMINE_URL}/users.json?name={email}",
    headers={"X-Redmine-API-Key": REDMINE_API_KEY},
)

redmine_user_id = None
if redmine_resp.status_code == 200:
    users = redmine_resp.json().get("users", [])
    for u in users:
        if u.get("mail") == email:
            redmine_user_id = u["id"]
            ok(f"redmine_user_id  : {redmine_user_id}  (login: {u.get('login')})")
            break
    if not redmine_user_id:
        warn("No Redmine user found with that email — redmine_user_id will be NULL.")
else:
    warn(f"Redmine lookup failed [{redmine_resp.status_code}] — redmine_user_id will be NULL.")


# ── Step 3: Upsert employee_master row ───────────────────────────────────────

step("Upserting employee_master record in Postgres")

engine = create_engine(DATABASE_URL)
now    = datetime.now(timezone.utc)

upsert_sql = text("""
    INSERT INTO employee_master (
        id,
        keycloak_user_id,
        redmine_user_id,
        first_name,
        last_name,
        user_email,
        designation,
        employee_id,
        status,
        onboarded_at,
        created_at,
        updated_at
    ) VALUES (
        gen_random_uuid(),
        :keycloak_user_id,
        :redmine_user_id,
        :first_name,
        :last_name,
        :user_email,
        :designation,
        :employee_id,
        'active',
        :now,
        :now,
        :now
    )
    ON CONFLICT (keycloak_user_id) DO UPDATE SET
        redmine_user_id = EXCLUDED.redmine_user_id,
        status          = 'active',
        onboarded_at    = COALESCE(employee_master.onboarded_at, EXCLUDED.onboarded_at),
        updated_at      = EXCLUDED.updated_at;
""")

# Also handle conflict on email in case user already exists without keycloak_user_id
check_sql = text("SELECT id, keycloak_user_id, redmine_user_id FROM employee_master WHERE user_email = :email")

with engine.begin() as conn:
    existing = conn.execute(check_sql, {"email": email}).fetchone()

    if existing:
        warn(f"Row already exists (id={existing[0]}) — updating keycloak/redmine IDs")
        conn.execute(text("""
            UPDATE employee_master SET
                keycloak_user_id = :keycloak_user_id,
                redmine_user_id  = COALESCE(:redmine_user_id, redmine_user_id),
                status           = 'active',
                onboarded_at     = COALESCE(onboarded_at, :now),
                updated_at       = :now
            WHERE user_email = :email
        """), {
            "keycloak_user_id": keycloak_user_id,
            "redmine_user_id":  redmine_user_id,
            "now":              now,
            "email":            email,
        })
        ok("Updated existing row.")
    else:
        conn.execute(upsert_sql, {
            "keycloak_user_id": keycloak_user_id,
            "redmine_user_id":  redmine_user_id,
            "first_name":       FIRST_NAME,
            "last_name":        LAST_NAME,
            "user_email":       email,
            "designation":      DESIGNATION,
            "employee_id":      EMPLOYEE_ID,
            "now":              now,
        })
        ok("Inserted new row.")

# ── Verify ───────────────────────────────────────────────────────────────────

step("Verifying inserted record")

with engine.connect() as conn:
    row = conn.execute(
        text("SELECT id, keycloak_user_id, redmine_user_id, status, user_email FROM employee_master WHERE user_email = :email"),
        {"email": email}
    ).fetchone()

if row:
    print()
    print(f"   id               : {row[0]}")
    print(f"   keycloak_user_id : {row[1]}")
    print(f"   redmine_user_id  : {row[2]}")
    print(f"   status           : {row[3]}")
    print(f"   user_email       : {row[4]}")
    print()
    ok("employee_master record is ready! 🎉")
else:
    fail("Could not verify the record — check DB manually.")
