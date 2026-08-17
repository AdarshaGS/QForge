"""Ed25519 signature verification for QForge license keys.

A separate keypair from update-signing (utils/update_signing.py) — a
license key and a release update are different trust domains, and a
compromise of one must not compromise the other.

PUBLIC_KEY_B64 is public by design — it ships with the app. The matching
private key is held offline by whoever issues licenses (see
scripts/issue_license.py) and must never be committed to this repo.
"""
import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PUBLIC_KEY_B64 = "dwTI/ti4x8wrCxuHvl7HnXQYhVcjsDHqVlP10oKFuVw="


def verify_signature(data: bytes, signature_b64: str) -> bool:
    """True iff signature_b64 (base64-encoded ed25519 signature) validates
    against *data* under PUBLIC_KEY_B64. Never raises."""
    try:
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY_B64))
        public_key.verify(base64.b64decode(signature_b64), data)
        return True
    except (InvalidSignature, ValueError):
        return False
