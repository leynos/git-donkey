"""Unit tests for the pure incoming and outgoing comparison policy."""

from __future__ import annotations

import pytest

from git_donkey import incoming_outgoing_policy


@pytest.mark.parametrize(
    "remote_names",
    [
        ["team", "team/core"],
        ["team/core", "team"],
    ],
)
def test_remote_name_for_ref_prefers_longest_match(remote_names: list[str]) -> None:
    """A nested remote must own the ref whatever the configured order."""
    owner = incoming_outgoing_policy.remote_name_for_ref(
        remote_names,
        "team/core/main",
    )

    assert owner == "team/core", (
        "the longest matching remote must own a nested remote ref"
    )


@pytest.mark.parametrize(
    ("remote_names", "ref", "expected"),
    [
        (["origin"], "origin", "origin"),
        (["origin"], "refs/remotes/origin/main", "origin"),
        (["team", "team/core"], "team/core", "team/core"),
        (["origin"], "main", None),
    ],
)
def test_remote_name_for_ref_matches_exact_and_prefixed_names(
    remote_names: list[str],
    ref: str,
    expected: str | None,
) -> None:
    """Exact names and slash-delimited prefixes keep selecting their remote."""
    owner = incoming_outgoing_policy.remote_name_for_ref(remote_names, ref)

    assert owner == expected, "the configured remote owning the ref must be returned"
