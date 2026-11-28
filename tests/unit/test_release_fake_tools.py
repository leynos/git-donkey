"""Fidelity of the release workflow's fake ``gh`` verifier.

The release steps run against a stand-in for the GitHub CLI. These tests pin
the verification policy the workflow relies on but which no single step can
exercise: the CLI's default predicate type and the override its flag allows.
"""

from __future__ import annotations

from tests.release_asset_provenance import (
    DEFAULT_REPOSITORY,
    DEFAULT_SIGNER_WORKFLOW,
    DEFAULT_SOURCE_DIGEST,
    DEFAULT_SOURCE_REF,
    approved_identity,
)
from tests.release_fake_tools import _matches_attestation_policy

_OTHER_PREDICATE = "https://example.test/other/v1"


def _arguments(predicate: str | None = None) -> list[str]:
    """Return a verifier call with the release's identity flags."""
    arguments = [
        "attestation",
        "verify",
        "asset.whl",
        "--repo",
        DEFAULT_REPOSITORY,
        "--signer-workflow",
        DEFAULT_SIGNER_WORKFLOW,
        "--source-ref",
        DEFAULT_SOURCE_REF,
        "--source-digest",
        DEFAULT_SOURCE_DIGEST,
        "--deny-self-hosted-runners",
    ]
    if predicate is not None:
        arguments.extend(("--predicate-type", predicate))
    return arguments


def test_the_verifier_defaults_to_the_slsa_predicate() -> None:
    """An omitted flag enforces the predicate type the CLI defaults to."""
    assert _matches_attestation_policy(approved_identity(), _arguments()), (
        "the approved SLSA identity must satisfy the default policy"
    )
    assert not _matches_attestation_policy(
        approved_identity(predicate_type=_OTHER_PREDICATE), _arguments()
    ), "another predicate type must not pass when the flag is omitted"


def test_an_explicit_predicate_override_is_honoured() -> None:
    """A named predicate type replaces the default."""
    assert _matches_attestation_policy(
        approved_identity(predicate_type=_OTHER_PREDICATE),
        _arguments(_OTHER_PREDICATE),
    ), "an explicit predicate type must match the record it names"
