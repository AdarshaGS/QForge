#!/usr/bin/env python3
"""Offline QForge Pro license issuer — a dev tool, never imported by the
app itself.

First time only, generate a keypair:

    python3 scripts/issue_license.py --keygen

This prints a PRIVATE key (store it somewhere safe *outside* this repo —
a password manager, not a file here — it must never be committed) and a
PUBLIC key (paste that into utils/license_signing.py's PUBLIC_KEY_B64).

To issue a license once you have a keypair, supply the private key via the
QFORGE_LICENSE_SIGNING_KEY env var (recommended — keeps it out of shell
history and process listings) or --private-key-file:

    QFORGE_LICENSE_SIGNING_KEY=<base64> python3 scripts/issue_license.py \\
        --email customer@example.com [--expires 2027-01-01]

Prints the pasteable license key string for the customer to paste into
Help → License… in the app.
"""
import argparse
import base64
import os
import sys
import uuid
from datetime import date, datetime, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.license_manager import build_key_string, canonical_payload_bytes  # noqa: E402


def _keygen():
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_b64 = base64.b64encode(
        private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
    ).decode("ascii")
    public_b64 = base64.b64encode(
        public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")

    print("Generated a new Ed25519 keypair.\n")
    print(f"PRIVATE (store outside this repo, never commit): {private_b64}")
    print(f"PUBLIC  (paste into utils/license_signing.py):   {public_b64}")


def _load_private_key(args) -> Ed25519PrivateKey:
    raw_b64 = os.environ.get("QFORGE_LICENSE_SIGNING_KEY")
    if not raw_b64 and args.private_key_file:
        with open(args.private_key_file) as f:
            raw_b64 = f.read().strip()
    if not raw_b64:
        sys.exit(
            "No signing key found. Set QFORGE_LICENSE_SIGNING_KEY or pass "
            "--private-key-file (run --keygen first if you don't have a keypair)."
        )
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw_b64))


def _issue(args):
    private_key = _load_private_key(args)

    if args.expires:
        # Validated up front so a typo fails loudly here, not silently at
        # activation time in the app.
        date.fromisoformat(args.expires)

    payload = {
        "license_id": uuid.uuid4().hex,
        "email": args.email,
        "edition": "pro",
        "issued_at": datetime.now(timezone.utc).date().isoformat(),
        "expires_at": args.expires,
    }
    signature = private_key.sign(canonical_payload_bytes(payload))
    signature_b64 = base64.b64encode(signature).decode("ascii")
    key_string = build_key_string(payload, signature_b64)

    print(f"License for {args.email}:\n")
    print(key_string)
    print(f"\n(license_id={payload['license_id']}, expires_at={args.expires or 'never'})")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keygen", action="store_true", help="Generate a new signing keypair and exit.")
    parser.add_argument("--email", help="Customer email to embed in the license.")
    parser.add_argument("--expires", help="Expiry date, YYYY-MM-DD. Omit for a perpetual license.")
    parser.add_argument("--private-key-file", help="Path to a file containing the base64 private key.")
    args = parser.parse_args()

    if args.keygen:
        _keygen()
        return

    if not args.email:
        parser.error("--email is required (or use --keygen to generate a keypair first)")

    _issue(args)


if __name__ == "__main__":
    main()
