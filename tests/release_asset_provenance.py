"""Small fixtures shared by the release-asset and provenance tests."""

from __future__ import annotations

import base64
import hashlib
import json
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib as pth

#: The predicate type the workflow's verifier calls name, and ``gh``'s default.
SLSA_PREDICATE = "https://slsa.dev/provenance/v1"

#: The identity the fake release attests by default.
DEFAULT_REPOSITORY = "leynos/git-donkey"
DEFAULT_TAG = "v1.2.3"
DEFAULT_SIGNER_WORKFLOW = f"{DEFAULT_REPOSITORY}/.github/workflows/release.yml"
DEFAULT_SOURCE_REF = f"refs/tags/{DEFAULT_TAG}"
DEFAULT_SOURCE_DIGEST = "a" * 40


def sha256_hex(content: bytes) -> str:
    """Return the SHA-256 hex digest of test bytes."""
    return hashlib.sha256(content).hexdigest()


def approved_identity(**overrides: object) -> dict[str, object]:
    """Return a build identity the release's verifier policy approves."""
    identity: dict[str, object] = {
        "repository": DEFAULT_REPOSITORY,
        "signer_workflow": DEFAULT_SIGNER_WORKFLOW,
        "source_ref": DEFAULT_SOURCE_REF,
        "source_digest": DEFAULT_SOURCE_DIGEST,
        "predicate_type": SLSA_PREDICATE,
        "self_hosted": False,
    }
    identity.update(overrides)
    return identity


def provenance_record(name: str, digest: str) -> list[dict[str, object]]:
    """Build a verifier result with one named digest subject."""
    return [
        {
            "verificationResult": {
                "statement": {"subject": [{"name": name, "digest": {"sha256": digest}}]}
            }
        }
    ]


def bundle_line(
    name: str,
    digest: str,
    *,
    identity: cabc.Mapping[str, object] | None = None,
) -> str:
    """Render one Sigstore-shaped JSON-lines bundle naming a subject.

    The line also carries the build identity the fake verifier checks. A real
    bundle proves its identity through its certificate; the fake reads this
    stand-in so that a supplied bundle, rather than the attestation store,
    decides which attestation is approved.

    Returns
    -------
    str
        One JSON line holding the DSSE envelope and the fake identity.
    """
    statement = {"subject": [{"name": name, "digest": {"sha256": digest}}]}
    payload = base64.b64encode(json.dumps(statement).encode()).decode()
    return json.dumps({
        "dsseEnvelope": {"payload": payload},
        "fakeIdentity": dict(approved_identity() if identity is None else identity),
    })


def bundle_records(
    path: pth.Path,
) -> list[tuple[set[tuple[str, str]], dict[str, object] | None]]:
    """Return each bundle line's subjects and fake identity.

    Malformed lines contribute nothing, matching the production reader's
    treatment of damaged evidence.

    Returns
    -------
    list[tuple[set[tuple[str, str]], dict[str, object] | None]]
        One ``(subjects, identity)`` pair per readable line; the identity is
        ``None`` when the line carries no fake identity.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    records: list[tuple[set[tuple[str, str]], dict[str, object] | None]] = []
    for line in content.splitlines():
        if not line.strip():
            continue
        try:
            document = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(document, dict):
            continue
        subjects = _line_subjects(document)
        identity = document.get("fakeIdentity")
        records.append((subjects, identity if isinstance(identity, dict) else None))
    return records


def _line_subjects(document: dict[str, object]) -> set[tuple[str, str]]:
    """Return the subjects named by one bundle line."""
    statement = _payload_statement(document)
    if statement is None:
        return set()
    subjects = statement.get("subject")
    if not isinstance(subjects, list):
        return set()
    return {pair for subject in subjects if (pair := _subject_pair(subject))}


def _payload_statement(document: dict[str, object]) -> dict[str, object] | None:
    """Return the in-toto statement a bundle line's envelope carries."""
    envelope = document.get("dsseEnvelope")
    payload = envelope.get("payload") if isinstance(envelope, dict) else None
    if not isinstance(payload, str):
        return None
    try:
        statement = json.loads(base64.b64decode(payload))
    except ValueError:
        return None
    return statement if isinstance(statement, dict) else None


def _subject_pair(subject: object) -> tuple[str, str] | None:
    """Return one subject's filename and lowercased digest, if well formed."""
    if not isinstance(subject, dict):
        return None
    name = subject.get("name")
    digest = subject.get("digest")
    sha256 = digest.get("sha256") if isinstance(digest, dict) else None
    if isinstance(name, str) and isinstance(sha256, str):
        return (name, sha256.lower())
    return None
