# Regression tests for GitHub issue #14: SSH-key-based tunnel auth crashed
# with "'NoneType' object has no attribute 'from_private_key_file'" for any
# private key that isn't parseable as plain RSA (e.g. many real-world .pem
# files), while a plain id_rsa key worked fine. Root cause: paramiko 3.0+
# removed DSSKey; QForge patched `paramiko.DSSKey = None` to compensate, but
# sshtunnel's own RSA -> DSS -> ECDSA -> Ed25519 private-key fallback loop
# calls `pkey_class.from_private_key_file(...)` on each type in turn and
# only catches paramiko.SSHException — so a bare None crashes with an
# unhandled AttributeError as soon as RSA parsing fails, instead of falling
# through to ECDSA/Ed25519 as intended.

import paramiko
import pytest
import sshtunnel

from services.db_service import _dsskey_stub_class, _ensure_dsskey_stub


def test_dsskey_stub_raises_sshexception_not_attributeerror():
    stub = _dsskey_stub_class(paramiko)
    with pytest.raises(paramiko.SSHException):
        stub.from_private_key_file("/nonexistent/path", password=None)


def test_ensure_dsskey_stub_is_idempotent_and_never_leaves_it_none():
    _ensure_dsskey_stub()
    _ensure_dsskey_stub()
    assert paramiko.DSSKey is not None
    assert hasattr(paramiko.DSSKey, "from_private_key_file")


def test_key_fallback_reaches_ecdsa_after_dsskey_slot_without_crashing(tmp_path):
    """End-to-end reproduction: a real, valid private key that is NOT RSA
    (so RSAKey.from_private_key_file fails first) must still load
    successfully via the DSS -> ECDSA fallback, instead of crashing on the
    DSSKey stand-in — this is exactly the failure mode from the .pem file
    in issue #14 (RSA parsing failed, then it crashed instead of trying the
    next key type)."""
    _ensure_dsskey_stub()

    key_path = tmp_path / "id_ecdsa"
    ecdsa_key = paramiko.ECDSAKey.generate()
    ecdsa_key.write_private_key_file(str(key_path))

    loaded = sshtunnel.SSHTunnelForwarder.read_private_key_file(str(key_path))
    assert loaded is not None
    assert isinstance(loaded, paramiko.ECDSAKey)
