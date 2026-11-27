"""Tests for reading Sigstore bundle subject coverage.

The attached bundle is trusted for completion only when its subjects name every
distribution the release holds. These tests pin the supported bundle shapes and
the fail-closed treatment of malformed or unreadable evidence.
"""

from __future__ import annotations

import base64
import json
import typing as typ

import pytest

from scripts.release_bundle import read_bundle_subjects, uncovered

if typ.TYPE_CHECKING:
    from pathlib import Path

_NAME = "git_donkey-1.0.0-py3-none-any.whl"
_SDIST = "git_donkey-1.0.0.tar.gz"
_DIGEST = "a" * 64


def _bundle(name: str, digest: str) -> str:
    """Render one Sigstore-shaped bundle naming ``name`` and ``digest``."""
    statement = {"subject": [{"name": name, "digest": {"sha256": digest}}]}
    payload = base64.b64encode(json.dumps(statement).encode()).decode()
    return json.dumps({"dsseEnvelope": {"payload": payload}})


def _write(path: Path, *lines: str) -> Path:
    """Write ``lines`` as a JSON-lines bundle."""
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_a_sigstore_bundle_names_its_subject(tmp_path: Path) -> None:
    """The DSSE payload's subject name and digest are read."""
    bundle = _write(tmp_path / "bundle.jsonl", _bundle(_NAME, _DIGEST))

    assert read_bundle_subjects([bundle]) == {(_NAME, _DIGEST)}, (
        "a Sigstore bundle must contribute its subject"
    )


def test_a_pretty_printed_single_bundle_names_its_subject(tmp_path: Path) -> None:
    """A single indented JSON bundle is parsed before the JSON-lines fallback."""
    statement = {"subject": [{"name": _NAME, "digest": {"sha256": _DIGEST}}]}
    payload = base64.b64encode(json.dumps(statement).encode()).decode()
    bundle = tmp_path / "bundle.json"
    bundle.write_text(
        json.dumps({"dsseEnvelope": {"payload": payload}}, indent=2),
        encoding="utf-8",
    )

    assert read_bundle_subjects([bundle]) == {(_NAME, _DIGEST)}, (
        "an indented single bundle must be read as one document"
    )


def test_a_verification_result_names_its_subject(tmp_path: Path) -> None:
    """The verifier's JSON shape is also understood, case-insensitively."""
    record = {
        "verificationResult": {
            "statement": {
                "subject": [{"name": _NAME, "digest": {"sha256": _DIGEST.upper()}}]
            }
        }
    }
    bundle = _write(tmp_path / "record.json", json.dumps(record))

    assert read_bundle_subjects([bundle]) == {(_NAME, _DIGEST)}, (
        "a verifier result must contribute its subject"
    )


@pytest.mark.parametrize(
    "content",
    ["{", "not json", "", json.dumps({"dsseEnvelope": {"payload": "!!"}})],
    ids=["truncated", "prose", "empty", "invalid-payload"],
)
def test_malformed_bundles_contribute_nothing(tmp_path: Path, content: str) -> None:
    """Damaged evidence never authorizes coverage."""
    bundle = tmp_path / "bundle.jsonl"
    bundle.write_text(content, encoding="utf-8")

    assert read_bundle_subjects([bundle]) == set(), (
        "a malformed bundle must contribute no subjects"
    )


def test_an_unreadable_bundle_contributes_nothing(tmp_path: Path) -> None:
    """A missing bundle file is not evidence of coverage."""
    assert read_bundle_subjects([tmp_path / "missing.jsonl"]) == set(), (
        "an unreadable bundle must contribute no subjects"
    )


def test_uncovered_reports_missing_and_mismatched_names() -> None:
    """Only a matching name and digest counts as covered."""
    assets = {
        _NAME: _DIGEST,
        _SDIST: "b" * 64,
        "git-donkey-v1.0.0-run1-1.attestations.jsonl": None,
    }

    assert uncovered(assets, {(_NAME, _DIGEST)}) == [_SDIST], (
        "a distribution with a different digest must be reported"
    )


def test_a_missing_digest_is_uncovered() -> None:
    """A release asset GitHub reports no digest for cannot be covered."""
    assert uncovered({_NAME: None}, {(_NAME, _DIGEST)}) == [_NAME], (
        "a missing digest must be reported as uncovered"
    )
