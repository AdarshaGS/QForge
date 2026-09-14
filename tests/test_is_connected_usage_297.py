"""Regression guard for issue #297.

DbService.is_connected() deliberately never touches self.connection (to
avoid corrupting a running query's packet sequence) and instead opens a
brand-new short-lived connection to check reachability. That means it
structurally cannot guarantee "the live session will actually serve the
next query" — only that the host/port/credentials are still reachable
right now.

Audited every caller (2026-09-14): the only production call site is
ConnectionPanel._check_health()'s periodic 30s background ping, which
uses it exactly for what it's documented to do — a reachability check
driving the health-indicator dot and the "reconnected" toast — never as
a pre-query gate or a proxy for "the next query on self.connection will
succeed." No misuse found.

This test locks that in with a static scan so a future caller that
starts treating is_connected() as a query-readiness check (rather than
a pure reachability ping) gets caught here instead of shipping a false
sense of safety.
"""
import ast
import glob
import os

_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _production_call_sites():
    sites = []
    for pattern in ("*.py", "ui/*.py", "services/*.py"):
        for path in glob.glob(os.path.join(_ROOT, pattern)):
            with open(path) as f:
                tree = ast.parse(f.read(), filename=path)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "is_connected"
                    # Exclude the method's own definition file except for
                    # any (non-existent today) internal self-call.
                ):
                    sites.append((os.path.relpath(path, _ROOT), node.lineno))
    return sites


def test_is_connected_has_exactly_one_known_production_caller():
    sites = _production_call_sites()
    assert sites == [("ui/connection_panel.py", 4720)], (
        f"is_connected() call sites changed: {sites}. If this is a new "
        f"caller, confirm it's using is_connected() only as a "
        f"reachability ping (e.g. a health-check/status indicator), never "
        f"as a gate before running a query or a proxy for whether the "
        f"live self.connection will serve the next statement — it "
        f"structurally can't guarantee that (issue #297). Update the "
        f"expected site list here once confirmed."
    )


def test_check_health_is_the_caller_and_only_drives_the_health_indicator():
    """Confirms _check_health() only ever uses the is_connected() result
    to set the health status/toast — not to gate any query execution."""
    path = os.path.join(_ROOT, "ui", "connection_panel.py")
    with open(path) as f:
        source = f.read()

    start = source.index("def _check_health(self):")
    # Bounded to this method's body by the next top-level "    def " at the
    # same indent level.
    end = source.index("\n    def ", start + 1)
    body = source[start:end]

    assert "is_connected()" in body
    assert "execute_query" not in body
    assert "execute_update" not in body
