"""Derives the public key from your local ARIEL_SCOPE_PRIVATE_KEY_FILE and
writes it into mcp/local.settings.json's ARIEL_SCOPE_PUBLIC_KEYS - the
private key content itself is never printed, logged, or sent anywhere; it's
read once, in this one process, purely to compute its public half.

Usage (from inside mcp/, with ARIEL_SCOPE_PRIVATE_KEY_FILE already set):
    python scripts/sync_local_scope_public_key.py [kid]

`kid` defaults to ARIEL_SCOPE_SIGNING_KID if set, else "ariel-signing-dev"
(webchat/server.py's own default) - must match whatever kid webchat is
actually signing with, or verification will fail with a "no matching key"
error, not a helpful one.
"""

import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization

_MCP_ROOT = Path(__file__).resolve().parent.parent
_SETTINGS_PATH = _MCP_ROOT / "local.settings.json"


def main() -> None:
    key_file = os.environ.get("ARIEL_SCOPE_PRIVATE_KEY_FILE")
    if not key_file:
        print("Set ARIEL_SCOPE_PRIVATE_KEY_FILE first, e.g.:")
        print('  $env:ARIEL_SCOPE_PRIVATE_KEY_FILE = "C:\\path\\to\\scope_private_key.pem"')
        sys.exit(1)

    kid = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ARIEL_SCOPE_SIGNING_KID", "ariel-signing-dev")

    with open(key_file, "rb") as f:
        private_key = serialization.load_pem_private_key(f.read(), password=None)

    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    settings = json.loads(_SETTINGS_PATH.read_text())
    existing = settings["Values"].get("ARIEL_SCOPE_PUBLIC_KEYS") or "{}"
    public_keys = json.loads(existing) if existing.strip() else {}
    public_keys[kid] = public_pem
    settings["Values"]["ARIEL_SCOPE_PUBLIC_KEYS"] = json.dumps(public_keys)
    _SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + "\n")

    print(f"Wrote the public key for kid={kid!r} into {_SETTINGS_PATH}")


if __name__ == "__main__":
    main()
