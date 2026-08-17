"""Ed25519 signature verification for release SHA256SUMS.txt (issue #113).

SHA256 alone only protects against transport corruption — an attacker who
can publish to (or intercept) the GitHub release can regenerate both the
DMG and SHA256SUMS.txt together, and the checksum still matches. Requiring
a valid signature over SHA256SUMS.txt, made with a private key that never
touches GitHub (kept only as the RELEASE_SIGNING_KEY Actions secret — see
.github/workflows/build-release.yml), means a compromised GitHub account
alone is no longer enough to slip in a tampered update.

PUBLIC_KEY_B64 is public by design — it goes with the app, not the secret.
"""
import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PUBLIC_KEY_B64 = "sltWa4K8LF27zSzLh+S/iNXFyfmj4/B+ESQNh01xF0c="


def verify_signature(data: bytes, signature_b64: str) -> bool:
    """True iff signature_b64 (base64-encoded ed25519 signature) validates
    against *data* under PUBLIC_KEY_B64. Never raises."""
    try:
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY_B64))
        public_key.verify(base64.b64decode(signature_b64), data)
        return True
    except (InvalidSignature, ValueError):
        return False
