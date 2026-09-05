# Flush Context

Paste this before closing a session or after finishing a chunk of work.

1. Update `ai-context/session-state.md`: what changed, what's left open, anything the next session needs to know that isn't obvious from `git diff`/`git log`.
2. If graphify-out/ exists and the change touched code structure meaningfully (new module, moved files), consider regenerating it: `/graphify`. Skip for small edits — it's not required per commit.
3. Sanity-check the diff (`git status`, `git diff`) for anything that shouldn't be committed (secrets, debug files, license-bypass edits — see `[No push: license bypass]` in memory).
4. Only commit if the user asked you to.

That's it — no gates, no required reviews. Use judgement for whether a change is worth a note in session-state.md at all; trivial fixes don't need one.
