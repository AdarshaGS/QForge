# QForge — AI Flush Context

Use this file to leave an accurate handoff when work on QForge pauses or ends.
It is deliberately a living record: replace the template sections with concrete
facts from the current task. Do not record passwords, access tokens, private
hostnames, customer data, or unredacted sensitive SQL.

## Current handoff

**Status:** `master` is at `8086157` (merge of PR #51). **v1.1.1 is tagged,
built, and publicly released**: https://github.com/AdarshaGS/QForge/releases/tag/v1.1.1
(`QForge.dmg` + `SHA256SUMS.txt`, built by CI from `8086157`). The working
tree is clean except the user's own pre-existing, unrelated
`graphify-out/manifest.json` change, which was left untouched throughout.

**Last updated:** 2026-08-05.

## Why v1.1.0/v1.1.1 originally broke (root cause, confirmed)

Both the original `v1.1.0` and `v1.1.1` tags pointed at the **same commit**
(`39dffd2`, the tip of the `fix/github-issues-batch` branch) — confirmed via
`gh run view --json headSha` on both tag-triggered `Build & Release` runs.
That commit was never on `master` at release time (`git merge-base
--is-ancestor 39dffd2 <master-at-the-time>` → false) and had not been through
the `Tests` workflow, which only triggers on `push: [master, main]` or
`pull_request` — **never on a tag push**. `Build & Release` (what tagging
actually invokes) only runs `pyinstaller --clean QForge.spec`, no test suite.
So the tag was pushed and released before any CI had run against that exact
code. Both releases were reverted (PRs #48, #49) and master was hard-restored
to the exact v1.0.9 tree (commit `2a26e3e`).

## What happened this session

1. Diagnosed the above by inspecting `gh run list`/`gh run view` history,
   `git merge-base`, and the two workflow YAMLs — no guessing.
2. Built the exact `39dffd2` snapshot locally (`build.sh` in a scratch git
   worktree, isolated `$HOME` so it never touched the user's real
   `~/Library/Application Support/QForge`) and confirmed it launches without
   crashing — ruled out a fatal frozen-only startup bug.
3. Reconstructed the batch on `master` via `git revert` of the two revert
   commits (`2fe0706`, `89d54d3`) rather than cherry-picking, so master's
   independent later fixes (dialog parenting, `sql_completer.py`'s
   `WindowDoesNotAcceptFocus` fix — both already on master, unrelated to this
   batch) merged cleanly instead of being clobbered.
4. Found and fixed two real bugs while re-auditing before re-shipping:
   - `ui/connection_panel.py` `_switch_database()` committed
     `self.config["database"]`/the pill label to the new database **before**
     confirming `select_db()`/reconnect succeeded. Verified directly against
     a local MySQL server: a failed `select_db()` leaves the live connection
     on the *previous* database while the UI would have already claimed the
     new one — every query would then silently run against the wrong
     database. Fixed to commit only after confirmed success.
   - `utils/credential_store.py` `set_password()`'s stale-Keychain-item
     recovery unconditionally force-deleted and retried on **any** failure,
     not just the specific `errSecInvalidOwnerEdit` signature it was written
     for — risking destroying a valid password on an unrelated failure (locked
     keychain, denied auth prompt). Gated on that signature now.
   - Dropped `.claude/settings.json`/`CLAUDE.md` that PR #45 had incidentally
     picked up from a graphify bootstrap in that old session — unrelated to
     the product change, not present on current master.
   - Bumped `APP_VERSION` to `1.1.1` (was stuck at `1.0.6` in source despite
     `v1.0.9` being the actual last release — pre-existing drift, now
     corrected as part of this bump).
5. **This time, went through the process the root-cause fix actually
   requires**: pushed a branch, opened PR #51, waited for `Tests` to pass on
   the PR, merged to master, waited for `Tests` to pass on master's own push,
   *then* tagged `v1.1.1` from that verified master commit. Confirmed
   `Build & Release` succeeded and the GitHub Release is live.

## Verified this session

- `pytest`: 43/43 passing (both before and after the hardening fixes).
- `py_compile` on every touched file.
- Real `build.sh` build of both the original `39dffd2` snapshot and the
  final hardened `v1.1.1` commit; both launch cleanly as packaged `.app`s
  with an isolated `$HOME`.
- Scripted, non-GUI verification against a real local MySQL 8.0 server
  (throwaway `qforge_repro_a`/`qforge_repro_b` databases, dropped after):
  `select_db()` to an existing database succeeds silently; `select_db()` to a
  nonexistent database raises `OperationalError` **and** leaves the
  connection on the previously-selected database — confirming the exact
  mismatch scenario the `_switch_database` fix closes.
- CI (`Tests`) green on the PR and on master's merge commit; `Build &
  Release` green on the `v1.1.1` tag; release published with
  `QForge.dmg` + `SHA256SUMS.txt`.

## Known, accepted limitations / not done this session

- **Not live-click-verified in the packaged `.app`**: the `Ctrl+T`/`P`/`N`/`Q`
  `QShortcut` removal in `main.py` (relies on the menu bar's own `QAction`
  shortcuts instead). Kept as-is — it matches the same
  full-screen-Space-switch bug family as two other fixes already
  independently present on master and trusted — but actual keystroke testing
  was skipped this session because the developer's real, production-connected
  QForge instance was open at the time and `System Events` keystroke
  automation cannot be reliably scoped away from whatever window is
  frontmost. Recommend a quick manual check after installing v1.1.1.
- No full manual click-through against a live MySQL/PostgreSQL server beyond
  the scripted `select_db()` check above (e.g. the SSH-tunnel paths, the new
  Database menu's create/drop flows, the self-update flow end-to-end against
  a real newer release).
- Production-safety Slices 4-6 from `ai/load-context.md` (transaction
  controls, query limits/timeout/cancellation, audit trail) — none started;
  unrelated to this session's scope.
- Broader product roadmap Tier 2 items (per prior sessions' plans) — Windows/
  Linux path verification, `pytest-qt` OS matrix in CI — still open;
  `CROSS_PLATFORM_SUPPORTED` in `build-release.yml` is still `false`.

## Exact next step

1. User installs `v1.1.1` from
   https://github.com/AdarshaGS/QForge/releases/tag/v1.1.1 and uses it
   day-to-day; report back anything that looks off, especially the
   MySQL fast-switch path and the four shortcuts noted above.
2. If a future release needs to skip the branch/PR ceremony for a trivial
   change, at minimum tag from a commit that is actually on `master` and has
   an actual green `Tests` run against it — the process gap that caused the
   original break (tagging an untested feature-branch tip directly) is now
   avoided by habit, not by any new CI enforcement; nothing currently stops
   someone from doing it again.
