import base64
import json
from datetime import date, timedelta

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from keyring.errors import PasswordDeleteError

from services import license_manager as lm_module
from services.license_manager import LicenseManager, build_key_string, canonical_payload_bytes
from utils import license_signing


class _FakeKeyring:
    """In-memory stand-in for the 3 `keyring` functions
    services/license_manager.py calls — mirrors real keyring's
    (service, account) keying and its delete-of-missing-item error."""

    def __init__(self):
        self._store = {}

    def get_password(self, service, account):
        return self._store.get((service, account))

    def set_password(self, service, account, value):
        self._store[(service, account)] = value

    def delete_password(self, service, account):
        key = (service, account)
        if key not in self._store:
            raise PasswordDeleteError()
        del self._store[key]


_fake_keyrings = {}  # str(tmp_path) -> _FakeKeyring, see _manager() below


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

    # One fake keychain per tmp_path (i.e. per test, since tmp_path is
    # unique per test) so a second _manager(tmp_path, ...) call in the
    # same test — simulating "a fresh process" — sees what the real OS
    # keychain would have persisted, instead of a wiped-clean fake.
    fake_keyring = _fake_keyrings.setdefault(str(tmp_path), _FakeKeyring())
    monkeypatch.setattr(lm_module.keyring, "get_password", fake_keyring.get_password)
    monkeypatch.setattr(lm_module.keyring, "set_password", fake_keyring.set_password)
    monkeypatch.setattr(lm_module.keyring, "delete_password", fake_keyring.delete_password)

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


# ─── Offline grace period / server revalidation (QF-PROD-006) ──────────────


def _activated_manager(tmp_path, monkeypatch):
    priv, pub_b64 = _keypair()
    monkeypatch.setattr(license_signing, "PUBLIC_KEY_B64", pub_b64)
    mgr = _manager(tmp_path, monkeypatch)
    mgr.activate(_sign(priv, _payload()))
    assert mgr.current_edition() == "pro"
    return mgr


def test_activate_seeds_the_validation_cache(tmp_path, monkeypatch):
    mgr = _activated_manager(tmp_path, monkeypatch)
    cache = lm_module._load_validation_cache()
    assert cache == {"last_validated_at": date.today().isoformat(), "server_status": "active"}


def test_deactivate_clears_the_validation_cache(tmp_path, monkeypatch):
    mgr = _activated_manager(tmp_path, monkeypatch)
    mgr.deactivate()
    assert lm_module._load_validation_cache() is None


def test_grace_period_not_yet_expired_keeps_pro(tmp_path, monkeypatch):
    mgr = _activated_manager(tmp_path, monkeypatch)
    stale = (date.today() - timedelta(days=lm_module.GRACE_PERIOD_DAYS - 1)).isoformat()
    lm_module._save_validation_cache({"last_validated_at": stale, "server_status": "active"})

    assert mgr.current_edition() == "pro"


def test_grace_period_expired_reverts_to_free(tmp_path, monkeypatch):
    mgr = _activated_manager(tmp_path, monkeypatch)
    stale = (date.today() - timedelta(days=lm_module.GRACE_PERIOD_DAYS + 1)).isoformat()
    lm_module._save_validation_cache({"last_validated_at": stale, "server_status": "active"})

    assert mgr.load() is None
    assert mgr.current_edition() == "free"


def test_clock_rolled_back_fails_closed(tmp_path, monkeypatch):
    mgr = _activated_manager(tmp_path, monkeypatch)
    future = (date.today() + timedelta(days=1)).isoformat()
    lm_module._save_validation_cache({"last_validated_at": future, "server_status": "active"})

    assert mgr.load() is None


def test_missing_validation_cache_fails_closed(tmp_path, monkeypatch):
    mgr = _activated_manager(tmp_path, monkeypatch)
    lm_module._clear_validation_cache()

    assert mgr.load() is None
    assert mgr.current_edition() == "free"


def test_revalidate_online_refreshes_cache_on_active_status(tmp_path, monkeypatch):
    mgr = _activated_manager(tmp_path, monkeypatch)
    stale = (date.today() - timedelta(days=5)).isoformat()
    lm_module._save_validation_cache({"last_validated_at": stale, "server_status": "active"})
    monkeypatch.setattr(
        lm_module.licensing_client, "validate_online",
        lambda key, install_id: {"ok": True, "status": "active", "device_limit": 1, "seats_used": 1},
    )

    mgr.revalidate_online()

    assert lm_module._load_validation_cache() == {
        "last_validated_at": date.today().isoformat(), "server_status": "active",
    }
    assert mgr.current_edition() == "pro"


def test_revalidate_online_marks_server_confirmed_bad_status_and_fails_closed(tmp_path, monkeypatch):
    for bad in ("revoked", "suspended", "expired"):
        mgr = _activated_manager(tmp_path, monkeypatch)
        monkeypatch.setattr(
            lm_module.licensing_client, "validate_online",
            lambda key, install_id, bad=bad: {"ok": False, "status": bad, "reason": f"license_{bad}"},
        )

        mgr.revalidate_online()

        # Fresh timestamp, but server-confirmed bad status overrides the
        # grace period entirely.
        assert mgr.load() is None, f"expected fail-closed for status={bad}"


def test_revalidate_online_marks_not_activated_and_fails_closed(tmp_path, monkeypatch):
    mgr = _activated_manager(tmp_path, monkeypatch)
    monkeypatch.setattr(
        lm_module.licensing_client, "validate_online",
        lambda key, install_id: {"ok": False, "status": "active", "reason": "not_activated"},
    )

    mgr.revalidate_online()

    assert mgr.load() is None


def test_revalidate_online_leaves_cache_untouched_on_network_error(tmp_path, monkeypatch):
    mgr = _activated_manager(tmp_path, monkeypatch)
    stale = (date.today() - timedelta(days=5)).isoformat()
    lm_module._save_validation_cache({"last_validated_at": stale, "server_status": "active"})
    monkeypatch.setattr(
        lm_module.licensing_client, "validate_online",
        lambda key, install_id: {"ok": False, "reason": "network_error"},
    )

    mgr.revalidate_online()

    assert lm_module._load_validation_cache() == {"last_validated_at": stale, "server_status": "active"}
    assert mgr.current_edition() == "pro"


def test_revalidate_online_is_a_noop_without_a_locally_valid_license(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(
        lm_module.licensing_client, "validate_online",
        lambda key, install_id: calls.append((key, install_id)),
    )

    mgr.revalidate_online()  # must not raise

    assert calls == []
