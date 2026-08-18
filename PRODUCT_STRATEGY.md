# QForge — Product & Business Strategy

Single cross-cutting view of product, UX, business, security, and go-to-market
decisions. Where a topic already has a dedicated doc, this file summarizes
and links out rather than duplicating: **[LAUNCH_PLAN.md](LAUNCH_PLAN.md)**
owns pricing/distribution tactics and cost math, **[SECURITY.md](SECURITY.md)**
owns the security/privacy commitment, **[ai/flush-context.md](ai/flush-context.md)**
owns engineering session state. Update this file directly rather than
appending — replace stale sections.

Status tags used below: **Done** (verified in code/CI), **Partial** (built
but incomplete), **Open decision** (needs you to choose), **Draft** (my
synthesis, not yet validated by you).

_Last updated: 2026-08-18._

---

## 1. Product

| Item | Status |
|---|---|
| What QForge is | **Done** — desktop SQL client for MySQL, PostgreSQL, SQLite. macOS-first (direct DMG, not Mac App Store — sandboxing would break SSH tunneling and Keychain access); Windows/Linux planned, not yet verified-supported. |
| Target users | Developers, data analysts — confirmed by the feature set (SQL editor, schema browser, ER diagrams, schema compare, query diff/verifier). |
| V1 scope | **Done** — MySQL (`pymysql`), PostgreSQL (`psycopg2`), SQLite (stdlib), all with SSH tunnel support except SQLite. |
| Core value | **Draft**, needs your validation: *"A native TablePlus/Postico-class SQL client at about half the price, with schema-compare and a query-diff/verifier that most competitors in this tier charge extra for or don't offer at all."* This is synthesized from your feature list + pricing stance — say if it's off. |

## 2. UX

- **Onboarding** — reframing your note: licensing isn't a gate that turns
  onboarding on/off. Free tier works with zero license, zero account, zero
  internet — install → connect → work. Licensing UX only appears later, as
  an *upgrade prompt* when you hit a Free limit or open the Upgrade dialog.
  So onboarding already exists (install → connect); what's actually
  undefined is whether there's a first-run tour/empty-state beyond "add a
  connection."
- **Daily workflow** — you said there isn't one, but README's "Core
  workflows" section already documents the real golden path: classify a
  connection's environment → explore schema → run SQL → edit data → explore
  ER diagram → compare schema. Worth treating this as *the* workflow for
  demo screenshots, a Show HN post, or a product walkthrough — it's more
  concrete than "no workflow."
- **Errors** — open, no dedicated audit yet. One concrete known gap already
  on record: Postgres's `_is_connection_error` doesn't match psycopg2's
  "connection already closed" message (found during recent test work, not
  yet fixed) — a real example of an error-UX gap worth triaging before V1
  ships broadly.
- **Performance** — checklist for a database client specifically:
  - Non-blocking query execution + cancel (README says cancel exists — confirm it doesn't freeze the UI thread on a slow query)
  - Paginated/virtualized result grids, never materializing a full result set in memory
  - Lazy schema introspection for large databases (don't eagerly walk every table/column at connect time)
  - Streaming import/export for large files — **already addressed**: the recent "streaming backend" commit for the export dialog covers this
  - Autocomplete responsiveness against schemas with thousands of tables
  - App startup time and steady-state memory over long sessions (query history, pinned tabs accumulating)
- **Polish → V2** — reasonable call, no objection.

## 3. Business

### Pricing — needs reconciliation before you sell anything

Three different numbers exist right now and they disagree:

| Source | Price |
|---|---|
| `services/entitlement_config.py` `PRICE_LABEL` (what the app actually shows in the Upgrade dialog) | `$49/lifetime` flat |
| `LAUNCH_PLAN.md` recommendation | $39–49 one-time, **or** $29/yr |
| `ai/flush-context.md` (most recent session state) | $49 single-device / $79 two-device lifetime, +$40/device/year for updates |

Your "half of TablePlus" framing checks out at the $49 number (TablePlus is
~$89 one-time per LAUNCH_PLAN.md's own comp table) — that part's fine. The
open task is picking **one** of these structures and making
`entitlement_config.py` match it, since that's the number customers
actually see.

### Free/Pro model — **Done**, freemium confirmed in code
`FREE_LIMITS` / `PRO_LIMITS` in `entitlement_config.py`: Free caps
connections/tabs/history/ER-diagram size; Pro unlocks those plus
`schema_compare` and `advanced_erd` as Pro-only features.

### Revenue model — explanation

"Pricing" is the sticker price; "revenue model" is how money actually
recurs. Four shapes to choose from, since QForge has no per-customer server
cost (it's a local app, not hosted SaaS), a few are viable:

1. **Pure one-time/lifetime** — simplest, but revenue is entirely
   front-loaded on new sales; no recurring line.
2. **One-time + optional annual update fee** — TablePlus/Sketch model. What
   the code is currently drifting toward (`+$40/device/year for updates`
   per flush-context). Buyer keeps a working perpetual copy; paying again
   only buys *new* major-version updates.
3. **Pure subscription** — steadier revenue, but subscription fatigue is a
   real objection for a single-purchase desktop tool, and your own
   LAUNCH_PLAN.md flags this as a reason to avoid it.
4. **Freemium + one-time Pro unlock** (what's actually implemented today) —
   Free is permanently usable, Pro is a one-time unlock plus the
   device-limit upsell. This is a variant of #2, not a separate model.

No team/company tier is in scope per `LAUNCH_PLAN.md` — worth restating
here so it doesn't quietly re-enter scope later.

### Costs

| Item | Your figure | What I found | Note |
|---|---|---|---|
| Apple Developer Program | ₹11,350/yr | $99/yr in LAUNCH_PLAN.md | Same line item — Apple's India-listed price includes tax and is commonly higher than a raw USD×rate conversion, not a discrepancy. |
| Infra | ₹1k/month | qforge-licensing is deployed on Railway | Plausible for a small Postgres + API service. |
| Not yet accounted for | — | From LAUNCH_PLAN.md: privacy policy/EULA review (~$50–1,500 depending on lawyer vs. template), payment-gateway take-rate once volume crosses your fee-free payout threshold, code-signing cert renewal, and support tooling if email stops scaling. | Worth a line item each even at $0 today. |

## 4. Payments

Precise current state (from `ai/flush-context.md`, the `qforge-licensing`
sibling repo): `/checkout` exists with a **demo/payment mode toggle**, but
**no payment provider is wired yet** and `/webhook/purchase` is a **501
stub**. So: scaffolding is real, production payment integration is not —
matches what you said, just spelling out exactly what's built vs. stubbed.
Invoicing: not implemented (depends on the webhook landing first).

**Open decision** worth confirming deliberately: Cashfree is a payment
*gateway* — you own GST/tax and invoicing yourself. LAUNCH_PLAN.md's
original suggestion was Paddle/Lemon Squeezy (merchant-of-record — they
handle VAT/tax and invoicing for a ~5%+$0.50 fee). Your Cashfree choice
looks deliberate given the payout-fee waiver below ₹20L/month, but it does
mean you're on the hook for invoicing/tax compliance yourself — worth
confirming that's the trade you want before wiring the webhook.

## 5. Licensing

| Piece | Status |
|---|---|
| Activation | **Done** — online, hits `qforge-licensing`'s `/activate`, enforces device limits server-side. |
| Entitlement | **Done** — offline-first; bundled defaults always work, optional remote override fetched once at startup. |
| Offline validation | **Done** — license signature (Ed25519) always verified locally, never requires network. |
| Device limits | **Done** — enforced online, at activation time. |

**The one pending item, precisely**: day-to-day edition checks
(`LicenseManager.load()` / `current_edition()`) are *purely local by
design* — only `/activate` and `/deactivate` ever touch the network. So a
license is checked online once, at activation, and never re-checked after
that. That means there's currently no way to remotely revoke a license
(e.g. after a refund/chargeback) once it's activated — it'll keep working
offline forever. That's a deliberate trade-off in the architecture, not a
bug, but you should decide consciously whether it's acceptable long-term or
whether periodic re-validation needs to be added later.

## 6. Security

- **DB credentials** — **Done**, Keychain-backed via `keyring`
  (`utils/credential_store.py`). One caveat worth knowing: if the OS
  keychain is ever unavailable, QForge falls back to plaintext in
  `connections.json` rather than silently losing the password, and warns
  the user when it happens. Rare, but "Keychain only" isn't 100% true —
  it's "Keychain, with a warned plaintext fallback."
- **SSH** — tunneling is supported for MySQL/Postgres (password or key
  auth per README); I haven't reviewed the SSH key-handling code in this
  pass — flag if you want that looked at specifically.
- **SQL privacy** — what you're really asking is "does any query text,
  schema, or data ever leave the machine." I checked: the only two outbound
  network integrations in the codebase are the `qforge-licensing` calls
  (license activate/deactivate, entitlement-config fetch) and the GitHub
  update-check. Neither carries query text, schema names, or row data —
  so the claim holds today. It's currently a design intent verified by
  reading the code, not something CI enforces — worth turning into an
  actual test (e.g., assert no network call in the codebase ever receives
  a SQL string or table name) so a future change can't silently break it.
- **Telemetry** — defined: automatically-collected usage/diagnostic data an
  app sends to a remote server (feature counts, crash dumps, sometimes
  query patterns). `SECURITY.md` already states QForge has **none** —
  confirmed by the same audit above. This is a real strength for a dev-tool
  audience, but it creates a direct tension with wanting funnel metrics —
  see §11.

## 7. Distribution

**Current state, verified in `.github/workflows/build-release.yml`**: macOS
build is **ad-hoc `codesign` only** — no Apple Developer ID, no
notarization. Today's DMG shows a Gatekeeper warning on first launch. This
is the real "last thing to do."

Already working, no action needed:
- DMG build fully automated in CI, triggered by version tags
- `SHA256SUMS.txt` signed with Ed25519 and checked by the self-updater
- Homebrew tap (`adarshags/homebrew-qforge`) auto-updated on every release

Remaining, per `LAUNCH_PLAN.md`'s own estimate:
- Enroll in Apple Developer Program (the ₹11,350/$99-per-year cost above), 24–48hr approval
- Replace ad-hoc `codesign` with real Developer-ID signing + notarization in CI (~1–2 dev days)
- Windows/Linux stay gated off (`CROSS_PLATFORM_SUPPORTED: 'false'`) until path-handling and per-OS verification land — not a distribution blocker, a support-scope decision

## 8. Engineering

- **Architecture** — **Done**, layered `services/` / `ui/` / `utils/` app on PySide6.
- **Testing** — this is further along than "dev testing happening": a real
  pytest suite (217+ tests referenced in the latest session, including live
  Postgres and SQLite integration tests that auto-skip if unreachable) runs
  in CI on every push/PR, plus `bandit` static security analysis. Worth
  updating your own mental model here — it's an automated suite, not manual spot-checks.
- **CI/CD** — **Done**: `tests.yml` (pytest + bandit on push/PR) and
  `build-release.yml` (tag-triggered build → sign → DMG → GitHub Release →
  Homebrew tap bump).
- **Compatibility** — checklist to define explicitly:
  - Minimum macOS version + Apple Silicon vs. Intel (confirm whether the current build is universal or arch-specific)
  - MySQL major versions (5.7 / 8.0 / 8.4) and Postgres major versions (12–17) your drivers are actually tested against
  - SQLite bundled-version behavior across OS Python builds
  - PySide6/Python upgrade path (pin vs. float)
  - Windows/Linux parity — explicitly out of scope until the CI comment's stated blockers are resolved

## 9. Operations

- **Website** — exists already, outside this repo, not verified here.
- **Documentation** — README.md is solid but developer-flavored (install,
  architecture, project layout). No separate end-user docs/FAQ exists yet —
  gap if non-technical buyers are part of the target (data analysts, per
  §1, may need this more than developers do).
- **Support** — only channel found is `SECURITY.md`'s vulnerability-report
  email. No general customer-support channel/process defined. **Open
  decision**, but low-stakes at this stage: a single support email is
  enough until volume says otherwise — don't over-build this early.
- **Crash reporting** — deliberately none, confirmed in `SECURITY.md` (a
  privacy commitment, not an oversight). Trade-off: field crashes are
  invisible unless a user emails you. Fine for now; revisit only with an
  explicit, opt-in mechanism if it becomes a real problem — see the
  telemetry tension in §11 before adding anything here.
- **Backups** — the actual backup surface isn't user data (that lives on
  each user's machine, not yours to hold). It's:
  1. `qforge-licensing`'s Postgres DB on Railway (license keys, activations,
     eventually purchase/invoice records) — confirm Railway's
     backup/point-in-time-recovery settings, or add a periodic `pg_dump`.
  2. The Ed25519 release-signing key and license-signing private key.
     Losing either blocks all future releases or license issuance —
     these need a real backup (e.g. password manager + an offline copy),
     not just "lives in a CI secret."

## 10. Marketing

- **Positioning** — see the Draft core-value statement in §1; validate or rewrite it first, everything else here follows from it.
- **Launch** — don't duplicate: `LAUNCH_PLAN.md` already has a scoped pricing/distribution/cost plan. Point future planning there.
- **Beta users / acquisition channels** — starter shortlist for a solo dev-tool launch, since you flagged this as a weak spot:
  - Show HN / Hacker News
  - r/SQL, r/Database, r/macapps
  - Product Hunt
  - "TablePlus alternative" / "Postico alternative" comparison posts — your own half-price positioning invites exactly that search
  - Homebrew — **correction**: the existing custom tap (`AdarshaGS/homebrew-qforge`) is *not* a cold-discovery channel. `brew search` cannot find an un-tapped third-party tap at all; it only helps someone who already knows QForge exists elsewhere. Added the one-line `brew install AdarshaGS/qforge/qforge` command to README's Quick Start (was missing entirely) so that install path is frictionless once someone *is* convinced. Real `brew search qforge` discoverability requires the official `homebrew-cask` repo, gated by a notability bar (self-submitted: 90 forks / 90 watchers / 225 stars — QForge is at 1/0/0 today). Treat as a **Track 2 milestone**: revisit once the channels above have driven real stars/forks, not something to attempt now.

## 11. Metrics

Downloads → Activation → Retention → Paid conversion is the standard
funnel; concretely for QForge: **Activation** = first successful DB
connection, **Retention** = still opening it a week/month later, **Paid
conversion** = Free → Pro.

**The real tension**: `SECURITY.md` commits to zero telemetry — so
Activation and Retention can't be measured the normal way (an in-app
analytics ping) without walking that commitment back. Two honest options,
not a recommendation I'll pick for you:

1. **Stay telemetry-free**, accept the blind spot on Activation/Retention,
   and lean on what's already free: GitHub release download counts,
   Homebrew install counts, and license-activation counts from the
   `qforge-licensing` DB (which gives you Paid conversion, and a rough
   proxy for "activated payers," for free — it's already server-side).
2. **Add a strictly opt-in (default OFF), anonymous, aggregate-only** event
   — e.g. "first successful connection," no query/schema/data content —
   the VS Code/Homebrew middle ground. This requires updating
   `SECURITY.md`'s current "no telemetry" claim publicly, so it's a trust
   decision as much as an engineering one.

---

## Where to keep this going forward

This file lives in the repo next to `LAUNCH_PLAN.md` and `SECURITY.md` —
same pattern you're already using, so it stays version-controlled,
diffable, and in the same place you (and I, in future sessions) already
look. For the one genuinely different piece — **§11 Metrics**, which is a
numeric time series (downloads/activations/conversion by week), not
narrative — a lightweight spreadsheet (Google Sheets) is a better fit than
Markdown; tracking numbers that change weekly in a git-diffed doc gets
noisy fast. Keep the narrative strategy here, the numbers there, and link
between them.
