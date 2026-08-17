import json
import urllib.error

from services import licensing_client


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _mock_urlopen(monkeypatch, response=None, raises=None):
    captured = {}

    def _fake(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = json.loads(req.data)
        captured["timeout"] = timeout
        if raises is not None:
            raise raises
        return _FakeResponse(response)

    monkeypatch.setattr(licensing_client.urllib.request, "urlopen", _fake)
    return captured


def test_activate_online_success(monkeypatch):
    captured = _mock_urlopen(monkeypatch, response={"ok": True, "receipt": "r", "device_limit": 2, "seats_used": 1})

    result = licensing_client.activate_online("the-key", "install-1")

    assert result == {"ok": True, "receipt": "r", "device_limit": 2, "seats_used": 1}
    assert captured["method"] == "POST"
    assert captured["url"] == f"{licensing_client.LICENSING_SERVICE_URL}/activate"
    assert captured["body"] == {"license_key": "the-key", "installation_id": "install-1"}


def test_activate_online_passes_through_device_limit_reached(monkeypatch):
    _mock_urlopen(monkeypatch, response={"ok": False, "reason": "device_limit_reached", "device_limit": 1, "seats_used": 1})

    result = licensing_client.activate_online("the-key", "install-1")

    assert result["ok"] is False
    assert result["reason"] == "device_limit_reached"


def test_activate_online_network_failure_returns_network_error(monkeypatch):
    _mock_urlopen(monkeypatch, raises=urllib.error.URLError("no connection"))

    result = licensing_client.activate_online("the-key", "install-1")

    assert result == {"ok": False, "reason": "network_error"}


def test_activate_online_timeout_returns_network_error(monkeypatch):
    _mock_urlopen(monkeypatch, raises=TimeoutError())

    result = licensing_client.activate_online("the-key", "install-1")

    assert result == {"ok": False, "reason": "network_error"}


def test_activate_online_malformed_response_returns_network_error(monkeypatch):
    def _fake(req, timeout=None):
        class _BadResponse:
            def read(self):
                return b"not json"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _BadResponse()

    monkeypatch.setattr(licensing_client.urllib.request, "urlopen", _fake)

    result = licensing_client.activate_online("the-key", "install-1")

    assert result == {"ok": False, "reason": "network_error"}


def test_deactivate_online_never_raises_on_network_failure(monkeypatch):
    _mock_urlopen(monkeypatch, raises=urllib.error.URLError("no connection"))
    licensing_client.deactivate_online("the-key", "install-1")  # must not raise


def test_deactivate_online_posts_to_deactivate_endpoint(monkeypatch):
    captured = _mock_urlopen(monkeypatch, response={"ok": True})

    licensing_client.deactivate_online("the-key", "install-1")

    assert captured["url"] == f"{licensing_client.LICENSING_SERVICE_URL}/deactivate"
    assert captured["body"] == {"license_key": "the-key", "installation_id": "install-1"}
