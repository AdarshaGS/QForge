# QForge — AI Flush Context

Leave an accurate, **concise** handoff here when work pauses — a few bullets
per item, not exhaustive prose. This file tracks *current state*, not
history: full root-cause writeups and verification detail belong in the
relevant GitHub issue's comments (posted as each fix lands) and in commit
messages once committed — don't duplicate them here. Replace stale sections
outright rather than appending to them. Do not record passwords, tokens,
hostnames, customer data, or unredacted SQL.

## Current state (as of 2026-08-17)

**Branch:** `master`. **Everything below is still uncommitted** — the
user's standing "hold" on this repo (no commits/pushes) has been in effect
the whole session and is still in effect. `git status` is the source of
truth for exactly which files.

**Milestone #12 (Licensing — Entitlements, #81–#86)** — unchanged from
before, all six done in code: entitlement model
(`services/entitlements.py`, `entitlement_config.py`, `license_manager.py`,
`utils/license_signing.py`, `utils/entitlement_fetcher.py`), Free
restrictions, Pro gates, upgrade UX (`ui/upgrade_dialog.py`,
`ui/license_dialog.py`). Pricing: $49 single-device / $79 two-device
lifetime, +$40/device/year for updates. Real on-screen `main.py` pass
still hasn't happened (only headless/widget-level verification so far).

**`qforge-licensing`** (sibling repo, pushed to GitHub, private — separate
from the hold above) is now fully built: `/activate`/`/deactivate`/admin
issuance/activations-lookup API (security-reviewed — constant-time admin
token check, device-limit race fixed), a marketing/pricing page, an admin
dashboard, a self-service device portal (license-key-only auth, no
accounts), and `/checkout` with a demo/payment mode toggle
(`QFORGE_CHECKOUT_MODE`). Postgres support added for Railway hosting.
48 tests passing. **Not deployed anywhere** (needs the user's own Railway
account) and **no payment provider chosen** (checkout stays in demo mode,
`/webhook/purchase` is a 501 stub).

**This repo now actually calls that service** (still part of the hold):
`utils/installation_id.py` (new), `services/licensing_client.py` (new —
`LICENSING_SERVICE_URL` is a placeholder until deployment), and
`services/license_manager.py`'s `activate()`/`deactivate()` now hit the
service for device-limit enforcement (`load()`/`current_edition()` stay
offline). `ui/license_dialog.py` runs activate/deactivate on a background
thread. Verified end-to-end against the local dev server: device limits
enforced, offline edition-check still works with the server stopped.

A **new Ed25519 keypair** now backs `utils/license_signing.py`'s
`PUBLIC_KEY_B64` (also uncommitted) — the original placeholder's private
key was never found/saved, so this fresh pair is now canonical across both
repos.

**New Postgres/SQLite test coverage**: `tests/test_db_service_postgresql.py`
(new, 45 tests, real integration against local Postgres, auto-skips if
unreachable) and `tests/test_db_service_sqlite.py` extended 28→42 (FKs,
PKs, indexes, DDL, views, multi-query, NULL/type handling, reconnect).
Found, not fixed: `_is_connection_error` doesn't match psycopg2's
"connection already closed" message (only the realistic server-killed-
session case). Full suite: 217/217 passing.

## Open threads not yet started

- Deploy `qforge-licensing` to Railway (user-driven — needs their account).
- Pick a payment provider; wire `/webhook/purchase`; flip
  `QFORGE_CHECKOUT_MODE` to `"payment"`.
- Once deployed: update `LICENSING_SERVICE_URL`, then commit/push — still
  blocked on the user lifting the hold.
- **#144** — decide "Advanced schema tools" Pro-gate scope.
- **#145** — arguably done now (the pricing page exists in
  `qforge-licensing`); still open, needs closing/updating once deployed.
- **Production-safety Slices 5–6** (`ai/load-context.md`) — unchanged,
  still pending.
- **#54, #75** — still on hold.
- **#61, #79, #66/#67** — still need a status recheck.
- **VAPT #113–120** / `LAUNCH_PLAN.md` — unchanged.
- The `_is_connection_error` gap found above (Postgres) — undecided
  whether it's worth fixing.

## Exact next step

1. User's call on lifting the hold, then a commit strategy for both the
   original milestone #12 work and this session's licensing-integration
   layer (likely separable).
2. Deploy `qforge-licensing` (Railway) — user-driven.
3. A real on-screen `main.py` pass — still hasn't happened for either the
   original entitlement UI or the new activate/deactivate network flow.

## Milestone status

**#12**: unchanged — #81–#86 done in code, #144/#145 open follow-ups.
The device-limit enforcement #144/#145 were partly blocking on is now
built (`qforge-licensing` + the `license_manager` integration), but none
of it is committed or deployed yet.

## Where the detail lives

Root-cause writeups, exact code changes, and verification steps for past
fixes are in each issue's GitHub comments and in commit messages. `git log`
and `gh issue view <n> --comments` are the source of truth for history —
this file is not.
