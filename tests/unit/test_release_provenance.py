"""Tests for parsing the verifier records that authorize release bytes.

The parser is deliberately narrow: it matches an exact filename and SHA-256
subject and leaves identity policy to the workflow's ``gh attestation verify``
flags. These tests pin that boundary and the fail-closed behaviour for
missing, malformed, and mismatching records.
"""

from __future__ import annotations

import json
import typing as typ

import pytest

from scripts import release_provenance
from tests.release_asset_provenance import provenance_record

if typ.TYPE_CHECKING:
    from pathlib import Path

_DIGEST = "a" * 64


def test_an_absent_record_reads_as_none(tmp_path: Path) -> None:
    """A missing verifier result is represented, not raised, on read."""
    assert (
        release_provenance.read_provenance_record(tmp_path / "missing.json") is None
    ), "a missing record must be reported as absent"


def test_a_valid_record_is_decoded(tmp_path: Path) -> None:
    """A verifier result decodes into its JSON value."""
    record = tmp_path / "record.json"
    record.write_text(json.dumps(provenance_record("g.whl", _DIGEST)), "utf-8")

    assert release_provenance.read_provenance_record(record) == provenance_record(
        "g.whl", _DIGEST
    ), "the decoded record must preserve the verifier output"


def test_invalid_json_fails_closed(tmp_path: Path) -> None:
    """A truncated or corrupt record cannot silently authorize an upload."""
    record = tmp_path / "record.json"
    record.write_text("{", encoding="utf-8")

    with pytest.raises(
        release_provenance.ProvenanceRecordError, match="not valid JSON"
    ):
        release_provenance.read_provenance_record(record)


def test_a_non_utf8_record_fails_closed(tmp_path: Path) -> None:
    """Bytes that are not UTF-8 are not a verifier result."""
    record = tmp_path / "record.json"
    record.write_bytes(b"\xff\xfe")

    with pytest.raises(
        release_provenance.ProvenanceRecordError, match="not valid UTF-8"
    ):
        release_provenance.read_provenance_record(record)


def test_an_unreadable_record_fails_closed(tmp_path: Path) -> None:
    """A directory where a record belongs is an unreadable record."""
    with pytest.raises(release_provenance.ProvenanceRecordError, match="cannot read"):
        release_provenance.read_provenance_record(tmp_path)


@pytest.mark.parametrize(
    ("name", "digest", "matches"),
    [
        ("g.whl", _DIGEST, True),
        ("g.whl", _DIGEST.upper(), True),
        ("other.whl", _DIGEST, False),
        ("g.whl", "b" * 64, False),
    ],
    ids=["matching", "case-insensitive", "other-name", "other-digest"],
)
def test_subject_matching_requires_the_exact_name_and_digest(
    name: str, digest: str, *, matches: bool
) -> None:
    """A record authorizes one filename and one digest, nothing else."""
    assert (
        release_provenance.has_matching_subject(
            provenance_record("g.whl", _DIGEST), name, digest
        )
        is matches
    ), "the subject must match the exact filename and digest"


@pytest.mark.parametrize(
    "document",
    [
        None,
        {},
        [],
        [{"verificationResult": {}}],
        [{"verificationResult": {"statement": {"subject": "not-a-list"}}}],
        [{"verificationResult": {"statement": {"subject": [{"name": "g.whl"}]}}}],
    ],
    ids=[
        "none",
        "object",
        "empty-list",
        "no-statement",
        "subject-not-list",
        "no-digest",
    ],
)
def test_malformed_documents_never_match(document: object) -> None:
    """Each untrusted JSON layer must have the expected shape."""
    assert not release_provenance.has_matching_subject(document, "g.whl", _DIGEST), (
        "a malformed verifier document must not authorize bytes"
    )


def test_requiring_a_missing_record_fails(tmp_path: Path) -> None:
    """An absent verifier result is a hard failure, not an empty pass."""
    with pytest.raises(
        release_provenance.ProvenanceRecordError, match="no verified build provenance"
    ):
        release_provenance.require_matching_record(
            tmp_path / "missing.json", "g.whl", _DIGEST
        )


def test_requiring_a_mismatched_record_fails(tmp_path: Path) -> None:
    """A record for another digest cannot authorize this file."""
    record = tmp_path / "record.json"
    record.write_text(json.dumps(provenance_record("g.whl", "b" * 64)), "utf-8")

    with pytest.raises(
        release_provenance.ProvenanceRecordError,
        match="no matching verified build provenance",
    ):
        release_provenance.require_matching_record(record, "g.whl", _DIGEST)


def test_a_matching_record_is_accepted(tmp_path: Path) -> None:
    """The matching record is the one case that does not raise."""
    record = tmp_path / "record.json"
    record.write_text(json.dumps(provenance_record("g.whl", _DIGEST)), "utf-8")

    release_provenance.require_matching_record(record, "g.whl", _DIGEST)
