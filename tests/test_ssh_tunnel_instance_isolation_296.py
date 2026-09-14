"""Regression tests for issue #296: does tearing down and rebuilding one
DbService's SSH tunnel on disconnect/reconnect race with, crash, or
silently corrupt another tab's in-flight query over "the same" tunnel?

Traced the architecture: it can't, because there is no shared SSH tunnel
to race over in the first place. self.ssh_tunnel (services/db_service.py)
is a plain per-instance attribute set in __init__ and assigned again in
_setup_ssh_tunnel() — every DbService instance opens and owns its own
independent SSHTunnelForwarder. Every query-execution path deliberately
gets its own dedicated DbService rather than reusing the panel's shared
self.db_service or another tab's instance:

  - Each SQL tab's Run/Run All uses a fresh DbService() per run
    (ConnectionPanel._run_query_in_tab), or its own _tx_db_service kept
    alive only across that one tab's transaction.
  - ConnectionPanel._run_bg_db() (mock data insert, CSV import, create/
    drop database, ...) explicitly documents "against a fresh dedicated
    DbService — never self.db_service", and already has its own
    dedicated regression test (tests/test_connection_panel_bg_db_237.py,
    issue #237) confirming exactly that.
  - ConnectionPanel._spawn_schema_fetch() explicitly documents "never
    touches self.db_service ... uses a dedicated connection".
  - The one background path that does touch self.db_service
    (_connect_in_background) is mutually excluded against the manual
    Reconnect action via the self._connecting guard (both check it).

So disconnect()/_reconnect() on one DbService instance's ssh_tunnel can
never be "the same" tunnel object another tab or background operation is
using — these tests lock that instance-isolation invariant in.
"""
from services.db_service import DbService


class _FakeTunnel:
    def __init__(self):
        self.stopped = 0

    def stop(self):
        self.stopped += 1


def test_disconnect_only_stops_this_instances_own_tunnel():
    a = DbService()
    b = DbService()
    a.connection = None
    b.connection = None
    tunnel_a, tunnel_b = _FakeTunnel(), _FakeTunnel()
    a.ssh_tunnel = tunnel_a
    b.ssh_tunnel = tunnel_b

    a.disconnect()

    assert tunnel_a.stopped == 1
    assert a.ssh_tunnel is None
    # b's tunnel is a completely separate object — untouched.
    assert tunnel_b.stopped == 0
    assert b.ssh_tunnel is tunnel_b


def test_two_instances_never_share_the_ssh_tunnel_attribute():
    a = DbService()
    b = DbService()
    assert a.ssh_tunnel is None and b.ssh_tunnel is None  # independent, not a shared default

    a.ssh_tunnel = _FakeTunnel()
    assert b.ssh_tunnel is None  # setting one instance's attribute never leaks to another
