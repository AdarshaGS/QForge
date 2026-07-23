from services.query_verifier import QueryVerifier


def test_maybe_limit_wraps_query_with_limit():
    sql = QueryVerifier._maybe_limit("SELECT * FROM users", 50)
    assert sql == "SELECT * FROM (SELECT * FROM users) _qlimit LIMIT 50"


def test_maybe_limit_strips_trailing_semicolon():
    sql = QueryVerifier._maybe_limit("SELECT * FROM users;", 10)
    assert sql == "SELECT * FROM (SELECT * FROM users) _qlimit LIMIT 10"


def test_maybe_limit_passthrough_when_limit_not_positive():
    sql = QueryVerifier._maybe_limit("SELECT * FROM users", 0)
    assert sql == "SELECT * FROM users"

    sql = QueryVerifier._maybe_limit("SELECT * FROM users", -5)
    assert sql == "SELECT * FROM users"


def test_maybe_limit_coerces_string_limit_to_int():
    # QSpinBox.value() always yields a real int in the app, but the guard
    # itself should not trust callers blindly — regression test for the
    # int() cast added to close a stringly-typed injection surface.
    sql = QueryVerifier._maybe_limit("SELECT * FROM users", "25")
    assert sql == "SELECT * FROM (SELECT * FROM users) _qlimit LIMIT 25"
