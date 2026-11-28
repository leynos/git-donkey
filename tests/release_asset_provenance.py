"""Small fixtures shared by the release-asset and provenance tests."""

from __future__ import annotations

import base64
import hashlib
import json


def sha256_hex(content: bytes) -> str:
    """Return the SHA-256 hex digest of test bytes."""
    return hashlib.sha256(content).hexdigest()


def provenance_record(name: str, digest: str) -> list[dict[str, object]]:
    """Build a verifier result with one named digest subject."""
    return [
        {
            "verificationResult": {
                "statement": {"subject": [{"name": name, "digest": {"sha256": digest}}]}
            }
        }
    ]


def bundle_line(name: str, digest: str) -> str:
    """Render one Sigstore-shaped JSON-lines bundle naming a subject."""
    statement = {"subject": [{"name": name, "digest": {"sha256": digest}}]}
    payload = base64.b64encode(json.dumps(statement).encode()).decode()
    return json.dumps({"dsseEnvelope": {"payload": payload}})
