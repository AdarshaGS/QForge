# QForge — AI Flush Context

Leave an accurate, **concise** handoff here when work pauses — a few bullets
per item, not exhaustive prose. This file tracks *current state*, not
history: full root-cause writeups and verification detail belong in the
relevant GitHub issue's comments (posted as each fix lands) and in commit
messages once committed — don't duplicate them here. Replace stale sections
outright rather than appending to them. Do not record passwords, tokens,
hostnames, customer data, or unredacted SQL.

## Current state (as of 2026-08-22)

**Branch:** `master`, clean working tree, in sync with `origin/master`
(`1431b94`). `git status` is the source of truth for exactly which files
are dirty — this section is a summary, not a substitute.

Everything the 2026-08-19 version of this file listed as "uncommitted"
(security fixes #161–163, export dialog #156–160, licensing integration
#81–88) landed on master in prior sessions — see `git log` (e.g. `e8bdedd`,
`e14206f`, `42443b1`, `b11f939`, `2703baf`). Trust `git log`/`gh issue
view --comments` over any stale summary here.

**Milestone #11 "Security — Post-Launch Hardening"** — this session's work.
All 3 issues closed by the user (2026-08-22), milestone has 0 open issues
but is **not yet closed as a milestone object** on GitHub — trivial next
step if wanted.
- **#141** (eval() in grid formula evaluator): already fixed on master
  before this session (`_safe_eval_arithmetic`, AST-whitelisted, in
  `ui/editable_table.py`). Verified this session with an adversarial
  payload test — no code change needed.
- **#120** (dependency CVE scanning in CI): added, this session
  (`1431b94`) — `pip-audit` step in
  `.github/workflows/tests.yml`, `pip-audit` pinned in
  `requirements-dev.txt`. Clean run against current `requirements.txt`.
- **#119** (keychain re-auth on ad-hoc → Developer ID signing migration):
  documented, this session (`1431b94`) — new "macOS: one-time re-auth
  after a signing change" section in `README.md`. `get_password`/
  `set_password` (`utils/credential_store.py`) already handle it
  gracefully; no code change needed, just verification + docs.

**Milestone #10 "Security — Pre-Launch VAPT (Critical)"** — 10/11 closed.
Only **#113** open: harden `utils/self_updater.py` against a compromised
GitHub release channel (verify code signature/Team ID, not just SHA256) —
blocked on real Apple Developer ID signing being in place first.

**Milestones #12 (Licensing — Entitlements) and #14 (Licensing — Purchase
Flow)** — fully closed (0 open). Not verified this session whether a real
production activation against an actually-issued signed license has ever
been done — flagged as open in a prior flush, status not rechecked here.

**Milestone #15 "Licensing — Website & Privacy"** — 7 open (#108, #109,
#185–189): domain/DNS, analytics, legal review of privacy policy/terms,
Cashfree production activation, Apple Developer account & code signing.
Untouched this session.

## Open threads not yet started

- Close milestone #11 itself on GitHub (all 3 issues done, milestone
  object still shows open).
- **#113** — auto-updater signature hardening; needs Developer ID signing
  first (tracked under milestone #15/#189).
- Milestone #15 (7 open items) — website/DNS/legal/payment-activation/
  signing chores, none started.
- **Production-safety Slices 5–6** (`ai/load-context.md`: query
  limits/timeout/cancellation, audit trail) — unchanged, untouched.
- Whether a real production license activation with an actually-issued
  signed key has happened — status unconfirmed, needs a recheck rather
  than assumed from an old note.

## Exact next step

1. If picking up security/hardening work: start with **#113** (needs
   Developer ID signing landed first) or close out milestone #15's
   remaining chores.
2. Otherwise, ask the user what's next — no committed work is currently
   in flight and no session left mid-task.

## Where the detail lives

Root-cause writeups, exact code changes, and verification steps for past
fixes are in each issue's GitHub comments and in commit messages. `git log`
and `gh issue view <n> --comments` are the source of truth for history —
this file is not.
