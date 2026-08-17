import base64
import json
from datetime import date, timedelta

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from services import license_manager as lm_module
from services.license_manager import LicenseManager, build_key_string, canonical_payload_bytes
from utils import license_signing


def _keypair():
    """A throwaway test keypair — never the real embedded one."""
    priv = Ed25519PrivateKey.generate()
    pub_b64 = base64.b64encode(
        priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    return priv, pub_b64


def _sign(priv, payload: dict) -> str:
    signature = priv.sign(canonical_payload_bytes(payload))
    return build_key_string(payload, base64.b64encode(signature).decode("ascii"))


def _payload(**overrides) -> dict:
    payload = {
        "license_id": "abc123",
        "email": "customer@example.com",
        "edition": "pro",
        "issued_at": "2026-01-01",
        "expires_at": None,
    }
    payload.update(overrides)
    return payload


def _manager(tmp_path, monkeypatch, activate_result=None) -> LicenseManager:
    monkeypatch.setattr(lm_module, "_LICENSE_FILE", str(tmp_path / "license.json"))
    # Default: the licensing service accepts every activation and
    # deactivation is a no-op — matches "a locally-valid key always
    # succeeds unless a test says otherwise", so existing signature/
    # expiry tests don't need to know the service exists at all.
    monkeypatch.setattr(
        lm_module.licensing_client, "activate_online",
        lambda key, install_id: activate_result or {"ok": True, "receipt": "test-receipt"},
    )
    monkeypatch.setattr(lm_module.licensing_client, "deactivate_online", lambda key, install_id: None)
    monkeypatch.setattr(lm_module, "get_installation_id", lambda: "test-installation-id")
    return LicenseManager()


def test_no_license_file_defaults_to_free(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    assert mgr.load() is None
    assert mgr.current_edition() == "free"


def test_activate_valid_signed_key_grants_pro(tmp_path, monkeypatch):
    priv, pub_b64 = _keypair()
    monkeypatch.setattr(license_signing, "PUBLIC_KEY_B64", pub_b64)
    mgr = _manager(tmp_path, monkeypatch)

    key = _sign(priv, _payload())
    ok, reason = mgr.activate(key)

    assert ok is True
    assert reason == ""
    assert mgr.current_edition() == "pro"
    assert mgr.load()["email"] == "customer@example.com"


def test_activation_persists_across_a_fresh_manager(tmp_path, monkeypatch):
    priv, pub_b64 = _keypair()
    monkeypatch.setattr(license_signing, "PUBLIC_KEY_B64", pub_b64)
    mgr = _manager(tmp_path, monkeypatch)
    mgr.activate(_sign(priv, _payload()))

    reloaded = _manager(tmp_path, monkeypatch)
    assert reloaded.current_edition() == "pro"


def test_tampered_payload_is_rejected(tmp_path, monkeypatch):
    priv, pub_b64 = _keypair()
    monkeypatch.setattr(license_signing, "PUBLIC_KEY_B64", pub_b64)
    mgr = _manager(tmp_path, monkeypatch)

    key = _sign(priv, _payload())
    payload_b64, signature_b64 = key.split(".", 1)
    tampered = json.loads(base64.urlsafe_b64decode(payload_b64))
    tampered["email"] = "attacker@evil.example"
    tampered_b64 = base64.urlsafe_b64encode(canonical_payload_bytes(tampered)).decode("ascii")
    tampered_key = f"{tampered_b64}.{signature_b64}"

    ok, reason = mgr.activate(tampered_key)

    assert ok is False
    assert reason != ""
    assert mgr.current_edition() == "free"


def test_key_signed_by_the_wrong_private_key_is_rejected(tmp_path, monkeypatch):
    _, pub_b64 = _keypair()
    other_priv, _ = _keypair()
    monkeypatch.setattr(license_signing, "PUBLIC_KEY_B64", pub_b64)
    mgr = _manager(tmp_path, monkeypatch)

    key = _sign(other_priv, _payload())
    ok, reason = mgr.activate(key)

    assert ok is False
    assert mgr.current_edition() == "free"


def test_expired_license_is_rejected(tmp_path, monkeypatch):
    priv, pub_b64 = _keypair()
    monkeypatch.setattr(license_signing, "PUBLIC_KEY_B64", pub_b64)
    mgr = _manager(tmp_path, monkeypatch)

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    key = _sign(priv, _payload(expires_at=yesterday))
    ok, reason = mgr.activate(key)

    assert ok is False
    assert "expired" in reason.lower()
    assert mgr.current_edition() == "free"


def test_not_yet_expired_license_is_accepted(tmp_path, monkeypatch):
    priv, pub_b64 = _keypair()
    monkeypatch.setattr(license_signing, "PUBLIC_KEY_B64", pub_b64)
    mgr = _manager(tmp_path, monkeypatch)

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    key = _sign(priv, _payload(expires_at=tomorrow))
    ok, _ = mgr.activate(key)

    assert ok is True
    assert mgr.current_edition() == "pro"


def test_malformed_key_string_is_rejected_without_raising(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    ok, reason = mgr.activate("this-is-not-a-license-key")
    assert ok is False
    assert reason != ""
    assert mgr.current_edition() == "free"


def test_non_pro_edition_in_payload_is_rejected(tmp_path, monkeypatch):
    priv, pub_b64 = _keypair()
    monkeypatch.setattr(license_signing, "PUBLIC_KEY_B64", pub_b64)
    mgr = _manager(tmp_path, monkeypatch)

    key = _sign(priv, _payload(edition="lifetime-super-admin"))
    ok, reason = mgr.activate(key)

    assert ok is False
    assert mgr.current_edition() == "free"


def test_deactivate_reverts_to_free(tmp_path, monkeypatch):
    priv, pub_b64 = _keypair()
    monkeypatch.setattr(license_signing, "PUBLIC_KEY_B64", pub_b64)
    mgr = _manager(tmp_path, monkeypatch)
    mgr.activate(_sign(priv, _payload()))
    assert mgr.current_edition() == "pro"

    mgr.deactivate()

    assert mgr.current_edition() == "free"
    assert mgr.load() is None


def test_deactivate_without_a_license_is_a_noop(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    mgr.deactivate()  # must not raise
    assert mgr.current_edition() == "free"


def test_a_bad_key_never_touches_the_stored_file(tmp_path, monkeypatch):
    priv, pub_b64 = _keypair()
    monkeypatch.setattr(license_signing, "PUBLIC_KEY_B64", pub_b64)
    mgr = _manager(tmp_path, monkeypatch)
    mgr.activate(_sign(priv, _payload()))
    assert mgr.current_edition() == "pro"

    mgr.activate("garbage-key-string")

    # The previously-activated Pro license must still be intact.
    assert mgr.current_edition() == "pro"


def test_a_locally_valid_key_is_never_written_before_the_server_confirms(tmp_path, monkeypatch):
    """A key with a perfectly valid signature must still fail closed if
    the server rejects it — local signature validity alone must never be
    enough to grant Pro."""
    priv, pub_b64 = _keypair()
    monkeypatch.setattr(license_signing, "PUBLIC_KEY_B64", pub_b64)
    mgr = _manager(tmp_path, monkeypatch, activate_result={"ok": False, "reason": "invalid_license"})

    ok, reason = mgr.activate(_sign(priv, _payload()))

    assert ok is False
    assert "not recognized" in reason
    assert mgr.current_edition() == "free"


def test_device_limit_reached_surfaces_a_specific_message(tmp_path, monkeypatch):
    priv, pub_b64 = _keypair()
    monkeypatch.setattr(license_signing, "PUBLIC_KEY_B64", pub_b64)
    mgr = _manager(
        tmp_path, monkeypatch,
        activate_result={"ok": False, "reason": "device_limit_reached", "device_limit": 2, "seats_used": 2},
    )

    ok, reason = mgr.activate(_sign(priv, _payload()))

    assert ok is False
    assert "2 device" in reason
    assert mgr.current_edition() == "free"


def test_network_error_fails_closed_with_a_clear_message(tmp_path, monkeypatch):
    priv, pub_b64 = _keypair()
    monkeypatch.setattr(license_signing, "PUBLIC_KEY_B64", pub_b64)
    mgr = _manager(tmp_path, monkeypatch, activate_result={"ok": False, "reason": "network_error"})

    ok, reason = mgr.activate(_sign(priv, _payload()))

    assert ok is False
    assert "reach the license server" in reason
    assert mgr.current_edition() == "free"


def test_deactivate_notifies_the_server_with_the_stored_key(tmp_path, monkeypatch):
    priv, pub_b64 = _keypair()
    monkeypatch.setattr(license_signing, "PUBLIC_KEY_B64", pub_b64)
    mgr = _manager(tmp_path, monkeypatch)
    key = _sign(priv, _payload())
    mgr.activate(key)

    calls = []
    monkeypatch.setattr(
        lm_module.licensing_client, "deactivate_online",
        lambda k, install_id: calls.append((k, install_id)),
    )

    mgr.deactivate()

    assert calls == [(key, "test-installation-id")]
    assert mgr.current_edition() == "free"


def test_deactivate_clears_local_state_even_if_the_server_is_unreachable(tmp_path, monkeypatch):
    priv, pub_b64 = _keypair()
    monkeypatch.setattr(license_signing, "PUBLIC_KEY_B64", pub_b64)
    mgr = _manager(tmp_path, monkeypatch)
    mgr.activate(_sign(priv, _payload()))

    def _raise(*args):
        raise RuntimeError("should never propagate")

    # deactivate_online is documented to swallow its own failures — this
    # simulates it doing so (returns None) even though the underlying
    # network call failed, which is licensing_client's real contract.
    monkeypatch.setattr(lm_module.licensing_client, "deactivate_online", lambda k, i: None)

    mgr.deactivate()

    assert mgr.current_edition() == "free"
    assert mgr.load() is None
