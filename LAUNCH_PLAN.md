# QForge — Launch Plan (macOS, individual/consumer sales)

Scope: Mac-only launch, sold directly to individuals (not companies). Not
legal advice — treat the dollar figures as planning estimates, not quotes.

## 1. Pricing

Comparable macOS SQL clients for reference: TablePlus (~$89 one-time per
platform, or $59.88/yr), Postico 2 ($40 one-time), Beekeeper Studio (free +
$9.95/mo), Sequel Ace (free/open source, no paid tier). QForge's feature set
(multi-DB, SSH tunnel, inline data editing, CSV/JSON/Excel import-export,
query diff/verifier, schema cache) lands in the Postico/TablePlus tier, not
the free-tool tier.

**Recommended structure:**

| Tier | Price | What's gated |
|---|---|---|
| Free trial | 14 days, full features | No credit card required — reduces checkout friction, standard for this category |
| Personal License | **$39–49 one-time**, OR **$29/yr** | One-time price gets current major version + 1 year of updates, then keeps working (perpetual license to last received version) — TablePlus/Postico model. Avoids subscription fatigue for an individual-buyer product. |
| No team/company tier | — | Explicitly out of scope per current direction; revisit only if inbound demand shows up. |

**Costs that eat into the sticker price** (factor into whichever number you pick):
- Merchant-of-record fee (Paddle / Lemon Squeezy): ~5% + $0.50 per transaction — covers VAT/sales tax handling, so this is cheaper than it looks once you account for not registering for tax yourself.
- No Apple App Store cut, since direct DMG distribution (not Mac App Store) is the right call here — sandboxing requirements would break SSH tunneling and keychain access.

## 2. Investment to build the launch pieces

| Item | Effort | Cash cost |
|---|---|---|
| Real code signing + notarization pipeline (replace ad-hoc `codesign` in `build.sh`/CI) | 1–2 dev days | $99/yr (Apple Developer Program) |
| Relicense off MIT (proprietary or open-core decision + LICENSE/README update) | 0.5–1 day | $0 |
| License-key generation + in-app validation, wired to Paddle/Lemon Squeezy checkout | 3–5 dev days | $0 upfront, ~5% of revenue per sale |
| EULA + privacy policy + refund policy | 1–2 days (template) or 1–2 weeks turnaround (lawyer review) | ~$50–100 (Termly/TermsFeed template) or $500–1500 (lawyer review) |
| Storefront / landing page | 2–4 dev days, or under a day using a MoR's hosted checkout page instead of a custom site | $0–~$20/mo if using a site builder |
| Auto-update hardening (verify signed releases, not just any GitHub asset) | 1 day | $0 |
| **Total** | **~9–15 dev days** (roughly 2–3 weeks full-time, 4–6 weeks part-time) | **~$150–300 first year DIY, up to ~$1,500–2,000 if lawyer-reviewed** |

## 3. Apple Developer ID + notarization timeline

Two different things get conflated under "Apple sign-off" — worth separating:

- **Apple Developer Program enrollment**: individual enrollment is usually
  approved in **24–48 hours** after identity verification (government ID +
  phone/2FA check). Occasionally slower (up to ~1–2 weeks) if Apple flags
  something for manual review, but that's the exception now, not the norm.
  Costs **$99/year**.
- **Notarization**: this is what actually lets a downloaded DMG open without
  a Gatekeeper warning. It is **fully automated** — Apple's notary service
  scans the signed binary and returns a result in **minutes** (occasionally
  up to a couple hours under load). There is **no human review team** for
  notarization, unlike App Store submissions.
- **There is no App Review step at all** for this launch plan, because we're
  shipping outside the Mac App Store. App Review (with its 24–48hr+ human
  review cycle, and possible rejection back-and-forth) only applies if you
  later decide to distribute through the Mac App Store instead of a direct
  DMG — not recommended here since Store sandboxing conflicts with SSH
  tunneling and keychain access QForge relies on.

**Bottom line**: budget **2–4 days** from "start enrollment" to "first
notarized, Gatekeeper-clean DMG" — almost all of that is your own signing
pipeline work, not waiting on Apple.
