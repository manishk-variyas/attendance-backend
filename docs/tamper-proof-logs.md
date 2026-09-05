# Tamper-Proof Logs

> A plain-language guide to what "tamper-proof logs" means, what our options
> are, and what we should actually do.

---

## 1. The three properties (often confused)

People say "tamper-proof" but mean different things. There are **three separate
properties**, and we should pick which one(s) we are solving for.

| Property | What it guarantees | How to achieve it |
|---|---|---|
| **Durability** | Logs are not accidentally deleted or rotated away | Append-only, no auto-delete, off-box copy |
| **Append-only** | Existing entries cannot be overwritten, truncated, or edited | `chattr +a`, write-only object store, WORM |
| **Integrity** | If someone *does* alter a log, we can prove it later | Hash chaining, signed digests, remote anchor |

---

## 2. Two hard truths

1. **On a single host, true tamper-proofing is impossible.**
   Anyone with `root` (or the `minioadmin` credential sitting on the same box)
   can reach everything. Every local measure is *hardening + detection*, not
   absolute prevention.

2. **The only genuinely tamper-proof approach is an out-of-band copy** — logs
   shipped to a system the local host/admin cannot reach (remote syslog over
   TLS, or cloud Object Lock / immutable storage). If we choose not to do this,
   that is our acknowledged ceiling.

---

## 3. The four layers (weakest → strongest)

### Layer 0 — Where we are today (below good practice)

- Loguru rotates logs and `retention` auto-deletes them after **14 / 30 days**
  (`apps/backend/app/core/logging.py`).
- A cron job force-deletes any log **larger than 50 MB hourly**, plus rotated
  files **older than 14 days daily**
  (`apps/backend/app/scripts/log-cleanup.cron`).
- Files are root-owned with `644` permissions; the host copies are `755`.

**Result:** no durability, no immutability, no integrity. The system actively
destroys audit evidence.

### Layer 1 — Stop destroying logs (durability)

- Remove the two `find -delete` cron rules.
- Set loguru `retention=None`.
- This is the cheapest, highest-value fix — it stops the bleeding now.

### Layer 2 — Append-only (prevent modification)

- `chattr +a` on `/app/logs/*.log` (kernel blocks truncate/overwrite except by root).
- A MinIO **write-only** `logs` bucket with **versioning** (dedicated `logwriter`
  user: no delete/overwrite; versioning keeps history).
- Tighten file ownership/permissions (app user, `640`, not root `644` / host `755`).

### Layer 3 — Integrity chaining (detect tampering)

- Per-line or per-block **hash chaining** (each entry hashes the previous + a secret).
- A periodic HMAC digest anchored to a more-protected location.
- Makes tampering *loudly evident* — but only after the fact.

### Layer 4 — Out-of-band copy (the real thing)

- Remote syslog over TLS **or** cloud Object Lock (Compliance mode) + versioning + encryption.
- Local tampering becomes irrelevant — the authoritative copy is unreachable locally.
- This is the only layer that is *truly* tamper-proof.

---

## 4. Recommended plan (phased)

### Phase 1 — Stop the bleeding *(do first, trivial, zero risk)*

1. Neutralize the `find -delete` rules in `scripts/log-cleanup.cron`.
2. Set `retention=None` in `core/logging.py`.
3. Tighten log file ownership and permissions.

### Phase 2 — Make modification hard *(prevention)*

4. MinIO write-only `logs` bucket + versioning + a dedicated `logwriter` user
   (reuses the existing MinIO / `StorageService`).
5. `chattr +a` on log files (requires the container to run as root or
   `CAP_LINUX_IMMUTABLE`).

### Phase 3 — Detect tampering *(integrity)*

6. Hash chaining in the log sink (or a post-rotation HMAC of each `.zip`).
7. A daily signed digest written to a restricted, separate location.

### Phase 4 — Document the ceiling *(future)*

8. Remote WORM (S3 Object Lock / GCS) as the eventual true-tamper-proof step.
   Design the log sink so switching endpoints is a config change, not a rewrite.

---

## 5. Which logs need which treatment

Not all logs need the same level of care:

| Log | Content | Priority | Treatment |
|---|---|---|---|
| `audit.log` | Security events (login, role changes, deletions, exports) | **Highest** | All layers |
| `error.log` | Exceptions / stack traces | Medium | Layers 1–2 |
| `access.log` | HTTP request logs | Low | Layers 1–2 |
| `sweeper.log`, `checkout_reminders.log`, `auto_close_attendance.log` | Cron task output | Low | Layers 1–2 |

A common pattern: apply the strict layers to `audit.log` first, then extend.

---

## 6. What this does *not* fix

- **Hiding logs inside the app** is a UX / least-privilege decision, not a
  security one. Anyone with filesystem/ops access can still read them.
- **App-level logs (access/error/sweeper)** are developer/ops artifacts. They
  should be read via `docker logs` or the dev-only `logdash` tool — not surfaced
  as product endpoints.

---

## 7. Current state (reference)

- Live logs live in the container volume `/app/logs`
  (`/var/lib/docker/volumes/infra_backend_logs/_data`).
- The host `apps/backend/logs/` folder is **stale / a different copy** (from a
  local non-Docker run) and is gitignored.
- Active log files: `access.log`, `audit.log`, `error.log`, `sweeper.log`, plus
  `checkout_reminders.log` and `auto_close_attendance.log`.
