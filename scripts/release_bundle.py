"""Read the subject digests recorded in Sigstore attestation bundles.

``publish-release`` assembles the attached bundle from ``gh attestation
download`` output. Before it trusts an existing bundle, it checks that the
bundle names every distribution the release holds, so a stale bundle cannot
stand in for a new or changed file.

The parser is deliberately narrow: it reads the in-toto statement's subject
names and SHA-256 digests from a Sigstore bundle's DSSE payload, and tolerates
the ``verificationResult`` shape ``gh attestation verify --format json``
returns. It does not verify signatures or trust; the workflow's verifier does
that.
"""

from __future__ import annotations

import base64
import json
import typing as typ

from scripts.release_assets import ATTESTATIONS_SUFFIX, is_bundle

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import pathlib as pth


def _subjects_from_statement(statement: object) -> set[tuple[str, str]]:
    """Return the filename/digest pairs one in-toto statement names."""
    if not isinstance(statement, dict):
        return set()
    subjects = statement.get("subject")
    if not isinstance(subjects, list):
        return set()
    found: set[tuple[str, str]] = set()
    for subject in subjects:
        if not isinstance(subject, dict):
            continue
        name = subject.get("name")
        digest = subject.get("digest")
        if not isinstance(name, str) or not isinstance(digest, dict):
            continue
        sha256 = digest.get("sha256")
        if isinstance(sha256, str):
            found.add((name, sha256.lower()))
    return found


def _subjects_from_envelope(envelope: object) -> set[tuple[str, str]]:
    """Return the subjects of one DSSE envelope, or an empty set."""
    if not isinstance(envelope, dict):
        return set()
    payload = envelope.get("payload")
    if not isinstance(payload, str):
        return set()
    try:
        statement = json.loads(base64.b64decode(payload))
    except ValueError:
        return set()
    return _subjects_from_statement(statement)


def _subjects_from_verification(verification: object) -> set[tuple[str, str]]:
    """Return the subjects of one verifier result, or an empty set."""
    if not isinstance(verification, dict):
        return set()
    return _subjects_from_statement(verification.get("statement"))


def _bundle_subjects(bundle: dict[str, object]) -> set[tuple[str, str]]:
    """Return the subjects named by one decoded bundle object."""
    return _subjects_from_envelope(bundle.get("dsseEnvelope")) | (
        _subjects_from_verification(bundle.get("verificationResult"))
    )


def _subjects_from_bundle(document: object) -> set[tuple[str, str]]:
    """Return the subjects of one Sigstore bundle or verifier result."""
    match document:
        case list() as results:
            return set().union(*(_subjects_from_bundle(item) for item in results))
        case dict() as bundle:
            return _bundle_subjects(bundle)
        case _:
            return set()


def _subjects_from_line(line: str) -> set[tuple[str, str]]:
    """Return the subjects in one JSON line, or an empty set."""
    if not line.strip():
        return set()
    try:
        document = json.loads(line)
    except json.JSONDecodeError:
        return set()
    return _subjects_from_bundle(document)


def _subjects_from_file(path: pth.Path) -> set[tuple[str, str]]:
    """Return the subjects in one bundle file, or an empty set."""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return set()
    try:
        return _subjects_from_bundle(json.loads(content))
    except json.JSONDecodeError:
        return set().union(
            *(_subjects_from_line(line) for line in content.splitlines())
        )


def read_bundle_subjects(paths: cabc.Iterable[pth.Path]) -> set[tuple[str, str]]:
    """Read every subject from the bundle files in ``paths``.

    Parameters
    ----------
    paths : collections.abc.Iterable[pathlib.Path]
        Bundle files, each a single JSON bundle or a JSON-lines file.

    Returns
    -------
    set[tuple[str, str]]
        Each ``(filename, sha256)`` pair a bundle subject names. Unreadable
        files and malformed lines contribute no subjects.
    """
    return set().union(*(_subjects_from_file(path) for path in paths))


def uncovered(
    assets: cabc.Mapping[str, str | None],
    subjects: cabc.Collection[tuple[str, str]],
) -> list[str]:
    """Return the distribution names no bundle subject covers.

    Parameters
    ----------
    assets : collections.abc.Mapping[str, str | None]
        The release's assets and their SHA-256 hex digests. Bundle assets are
        ignored; a name whose digest GitHub does not report is uncovered.
    subjects : collections.abc.Collection[tuple[str, str]]
        Each ``(filename, sha256)`` pair the bundles name.

    Returns
    -------
    list[str]
        The uncovered distribution names, sorted.
    """
    return [
        name
        for name, digest in sorted(assets.items())
        if not is_bundle(name) and (digest is None or (name, digest) not in subjects)
    ]


def combined_coverage(
    assets: cabc.Mapping[str, str | None],
    paths: cabc.Iterable[pth.Path],
) -> list[str]:
    """Return the uncovered names for the best single combined bundle.

    Coverage is not accumulated across bundles: one combined bundle must name
    every distribution, because that is the bundle a consumer downloads. A
    single-run ``.sigstore.json`` bundle covers only the run that produced it
    and is not considered.

    Parameters
    ----------
    assets : collections.abc.Mapping[str, str | None]
        The release's assets and their SHA-256 hex digests.
    paths : collections.abc.Iterable[pathlib.Path]
        Candidate bundle files.

    Returns
    -------
    list[str]
        The uncovered distribution names for the best combined bundle, or for
        every distribution when no combined bundle exists.
    """
    combined = [path for path in paths if path.name.endswith(ATTESTATIONS_SUFFIX)]
    if not combined:
        return uncovered(assets, set())
    return min(
        (uncovered(assets, read_bundle_subjects([path])) for path in combined),
        key=len,
    )
