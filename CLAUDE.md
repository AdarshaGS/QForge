# qforge

## Ignore venv

Never read, search, edit, or otherwise operate on files under `venv/`. Exclude it from greps, globs, and file exploration — it's third-party installed packages, not project code.

## Search order: codegraph/graphify first

This repo has `.codegraph/` (init'd) and `graphify-out/` (built). For any "where is X", "what calls Y", "how does Z work" question, or before editing a symbol:

1. Try `codegraph_explore` (MCP tool) or `codegraph explore "<query>"` (shell) first.
2. If graphify's knowledge graph (`graphify-out/`) is more current or the question is architectural/cross-file, query that instead (graphify skill).
3. Only fall back to Grep/Glob/plain Read/Bash search when both come back empty or the project's index is missing/stale for the file in question.

Don't reach for grep/find as the first move — codegraph and graphify already index this repo and answer faster with less token spend. Same rule applies inside subagents (explorer, bug-fixer, code-reviewer, etc.) — they should try codegraph/graphify before their own Grep/Glob tools.
