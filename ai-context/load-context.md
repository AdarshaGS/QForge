# Load Context

Paste this at the start of a session (or whenever you're picking work back up).

1. Read `ai-context/session-state.md` — what was in progress last time, and any open TODOs.
2. Read `MEMORY.md` (repo root) and `graphify-out/GRAPH_REPORT.md` if present — architecture/knowledge-graph summary.
3. Run `git status` and `git log --oneline -10` to see uncommitted work and recent history.
4. If the question is about how QForge's code fits together (module relationships, "what calls X", "where does Y live"), query the knowledge graph first: `graphify query "<question>" --graph graphify-out/graph.json`. Fall back to grep/Read only if the graph doesn't answer it.

Then state in one line what you're picking up, and start.
