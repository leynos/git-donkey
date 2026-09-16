"""Unit tests for the shared stack record's format and its pure decisions.

Three commands read and write one record, so the format has to survive being
written today and read by a later revision. These tests pin the parts that
would otherwise diverge silently: the versioned parent values, the four
configuration keys Git will hand back in lower case, the reconciliation of
configuration against the anchor ref, and the decision that says whether a
branch created from a base is stacked at all. Everything here is pure — no
repository, no Git, no filesystem. The store that puts these decisions on disk
is covered by the ``stack_store`` suites; the lifecycle they compose into is in
``tests/integration/test_stack_record_lifecycle.py``.
"""

from __future__ import annotations

import shutil

# Git's own ref checker is the only oracle for what Git accepts.
import subprocess  # ruff: ignore[suspicious-subprocess-import]

import pytest

from git_donkey import stack_records

_SEED = "0" * 40
_OTHER = "1" * 40


def _config(**values: str) -> dict[str, str]:
    """Build a branch configuration section holding the named record keys.

    A key that names a ``RecordKey`` member is written under that key's
    configuration name, so a test asks for ``parent`` and gets the key Git
    hands back for it; any other key is written as it was given, which is how
    a section picks up a setting of its own.

    Parameters
    ----------
    **values : str
        Values to write, keyed by record key name or configuration name.

    Returns
    -------
    dict[str, str]
        The configuration section.

    """
    members = stack_records.RecordKey.__members__
    return {
        members[name.upper()].value if name.upper() in members else name: value
        for name, value in values.items()
    }


def _complete_config(**overrides: str) -> dict[str, str]:
    """Return a configuration section holding every record key."""
    values = {
        "parent": "v1:branch:parent",
        "base": _SEED,
        "recorded_from": _OTHER,
        "evidence": "stack-record-birth",
    }
    values.update(overrides)
    return _config(**values)


@pytest.mark.parametrize(
    ("parent", "expected"),
    [
        (
            "v1:branch:parent",
            stack_records.StackParent(branch="parent", pull_request=None),
        ),
        (
            "v1:pr:owner/repository#123",
            stack_records.StackParent(
                branch=None,
                pull_request=stack_records.PullRequestIdentity(
                    repository="owner/repository", number=123
                ),
            ),
        ),
    ],
    ids=["branch", "pull-request"],
)
def test_parent_values_round_trip_through_render_and_parse(
    parent: str, expected: stack_records.StackParent
) -> None:
    """A rendered parent must parse back to itself, or a record cannot persist."""
    assert stack_records.parse_parent(parent) == expected, (
        "the versioned form parses to the parent it names"
    )
    assert stack_records.render_parent(expected) == parent, (
        "rendering is the exact inverse of parsing"
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "branch:parent",
        "v2:branch:parent",
        "v1:tag:parent",
        "v1:branch:",
        "v1:pr:owner/repository",
        "v1:pr:owner#123",
        "v1:pr:owner/repository#abc",
        "v1:pr:owner/repository#123#456",
        "v1:pr:owner/repository#0",
        "v1:pr:owner/repository#²",
        "v1:pr:owner/repository#٣",
    ],
    ids=[
        "empty",
        "unversioned",
        "unknown-version",
        "unknown-kind",
        "empty-branch",
        "no-number",
        "no-owner",
        "non-numeric-number",
        "two-numbers",
        "zero-number",
        "superscript-number",
        "arabic-indic-number",
    ],
)
def test_unparsable_parent_values_are_rejected_rather_than_guessed(value: str) -> None:
    """An unrecognized value must not be read as some nearby valid one."""
    assert stack_records.parse_parent(value) is None, (
        "a value outside the versioned grammar yields no parent"
    )


def test_a_number_with_a_leading_zero_is_still_a_number() -> None:
    """A leading zero is part of the decimal form, so ``001`` is the count one.

    ``int`` reads the form that way, and rendering is what writes a number
    back, so a hand-written ``#001`` is a number this grammar accepts and one
    a later render normalizes to ``#1``.
    """
    assert stack_records.parse_parent("v1:pr:owner/repository#001") == (
        stack_records.StackParent(
            branch=None,
            pull_request=stack_records.PullRequestIdentity(
                repository="owner/repository", number=1
            ),
        )
    ), "a leading zero is part of the count, not a different number"


def test_record_keys_are_the_lower_case_spellings_git_returns() -> None:
    """Git lower-cases variable names, so the canonical keys must be lower case."""
    assert {key.value for key in stack_records.RecordKey} == {
        "stackparent",
        "stackbase",
        "stackbaserecordedfrom",
        "stackbaseevidence",
    }, "the canonical spellings are the ones a config read returns"


def test_record_keys_are_accepted_whatever_case_the_configuration_uses() -> None:
    """A configuration read that preserved case must still reconcile."""
    shouted = {key.upper(): value for key, value in _complete_config().items()}

    result = stack_records.reconcile("child", shouted, _SEED, branch_exists=True)

    assert isinstance(result, stack_records.StackRecord), (
        "lookups are case-insensitive, so a shouted section is still a record"
    )


def test_a_branch_with_neither_artefact_has_no_record() -> None:
    """Most branches are not stacked, so absence is ordinary rather than an error."""
    result = stack_records.reconcile("plain", {}, None, branch_exists=True)

    assert result == stack_records.RecordAbsent(), (
        "neither configuration nor anchor is the absent state"
    )


def test_configuration_without_an_anchor_is_still_a_record() -> None:
    """Config is authoritative for values, so a lost anchor loses reachability only."""
    result = stack_records.reconcile(
        "child", _complete_config(), None, branch_exists=True
    )

    assert isinstance(result, stack_records.StackRecord), (
        "an absent anchor is a boundary at risk, not a malformed record"
    )
    assert result.base == _SEED, "the configuration still names the boundary"


def test_an_anchor_agreeing_with_the_configuration_is_a_record() -> None:
    """The ordinary live record: both artefacts present and consistent."""
    result = stack_records.reconcile(
        "child", _complete_config(), _SEED, branch_exists=True
    )

    assert isinstance(result, stack_records.StackRecord), (
        "agreeing artefacts reconcile to a usable record"
    )
    assert result.branch == "child", "the record names the branch it was read for"
    assert result.parent == stack_records.StackParent(
        branch="parent", pull_request=None
    ), "a terminal parent names a branch and no pull request"
    assert result.recorded_from == _OTHER, "the observed child tip is preserved"
    assert result.evidence == "stack-record-birth", "the evidence kind is preserved"


def test_an_anchor_disagreeing_with_the_configuration_is_malformed() -> None:
    """Two artefacts that disagree are reported, never resolved by preferring one."""
    result = stack_records.reconcile(
        "child", _complete_config(), _OTHER, branch_exists=True
    )

    assert isinstance(result, stack_records.RecordMalformed), (
        "a disagreement is malformed rather than a coin toss"
    )
    assert _SEED in result.reason or _OTHER in result.reason, (
        "the reason names the values that disagreed"
    )


def test_an_anchor_without_configuration_is_malformed() -> None:
    """`git branch -m` carries configuration and leaves the anchor behind (AXIOM-11)."""
    result = stack_records.reconcile("child", {}, _SEED, branch_exists=True)

    assert isinstance(result, stack_records.RecordMalformed), (
        "an anchor alone is a half-record, not a record"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"parent": "v2:branch:parent"},
        {"parent": "branch:parent"},
        {"base": "abc1234"},
        {"base": ""},
        {"recorded_from": "not-an-object-id"},
        {"evidence": ""},
    ],
    ids=[
        "unknown-version",
        "unversioned",
        "short-id",
        "empty-base",
        "bad-tip",
        "empty-kind",
    ],
)
def test_an_incomplete_or_unparsable_section_is_malformed(
    overrides: dict[str, str],
) -> None:
    """A half-written record must be reported rather than read as no record."""
    result = stack_records.reconcile(
        "child", _complete_config(**overrides), _SEED, branch_exists=True
    )

    assert isinstance(result, stack_records.RecordMalformed), (
        "a value that does not parse is malformed"
    )


def test_unrelated_configuration_keys_are_not_part_of_the_record() -> None:
    """A branch section holds other settings; they must not be read as a record."""
    result = stack_records.reconcile(
        "child",
        _config(**{"description": "a branch with settings of its own"}),
        None,
        branch_exists=True,
    )

    assert result == stack_records.RecordAbsent(), (
        "keys the record does not name are ignored"
    )


def test_a_record_whose_branch_is_gone_is_orphaned() -> None:
    """A record outliving its branch is reported and never used as evidence."""
    result = stack_records.reconcile(
        "deleted", _complete_config(), _SEED, branch_exists=False
    )

    assert isinstance(result, stack_records.RecordOrphaned), (
        "no branch means no record to use, whatever the artefacts say"
    )
    assert result.branch == "deleted", "the orphan names the branch that is gone"


@pytest.mark.parametrize(
    ("base_ref", "base_commit", "expected"),
    [
        ("main", "a", False),
        ("refs/heads/main", "a", False),
        ("origin/main", "a", False),
        ("refs/remotes/origin/main", "a", False),
        ("feature/parent", "a", False),
        ("feature/parent", "b", True),
        ("origin/feature-parent", "b", True),
        (_OTHER, "b", True),
        ("main", "c", False),
    ],
    ids=[
        "trunk-by-name",
        "trunk-by-full-name",
        "remote-tracking-at-trunk",
        "remote-tracking-full-at-trunk",
        "branch-at-trunk-commit",
        "branch-ahead-of-trunk",
        "remote-branch-ahead-of-trunk",
        "explicit-commit-ahead-of-trunk",
        "trunk-by-name-that-moved",
    ],
)
def test_a_branch_is_stacked_unless_its_base_resolves_to_the_trunk(
    base_ref: str, base_commit: str, *, expected: bool
) -> None:
    """INV-11's whole decision, as a table rather than a repository fixture."""
    result = stack_records.should_record(base_ref, base_commit, "main", "a")

    assert result is expected, (
        "a branch created at the trunk commit is not stacked, whatever it is called"
    )


def test_both_ref_paths_are_built_from_the_same_validated_component() -> None:
    """The two namespaces differ only in their prefix, so they cannot drift."""
    assert stack_records.base_ref_path("feature/child") == (
        "refs/stack-bases/feature/child"
    ), "the anchor ref keeps the branch's own hierarchical name"
    assert stack_records.tombstone_ref_path("feature/child") == (
        "refs/stack-tombstones/feature/child"
    ), "the tombstone ref mirrors the anchor's layout"


_GIT_REFUSES = [
    "",
    "a:b",
    "a b",
    "a..b",
    "a.lock",
    "a.lock/x",
    "a.",
    ".a",
    "a?",
    "a*",
    "a[",
    "a\\b",
    "a^",
    "a~",
    "a@{0}",
    "a/b:c",
    "/a",
    "a/",
    "a//b",
    "a/.b",
    "a/b.",
    "a\x7f",
    "a\n",
]

_GIT_ACCEPTS = [
    "main",
    "feature/child",
    "a/b/c",
    "issue-123-fix",
    "release-1.2.3",
    "a_b",
    "A",
    "@",
    "a.LOCK",
]


def _git_accepts_ref(ref: str) -> bool:
    """Return whether Git's own ref name checker accepts ``ref``."""
    git = shutil.which("git")
    if git is None:
        pytest.fail("this test requires Git on PATH to be an oracle")
    # Resolved executable, no shell.
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [git, "check-ref-format", ref],
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0


@pytest.mark.parametrize("value", _GIT_REFUSES, ids=repr)
def test_every_name_git_refuses_is_refused_here_too(value: str) -> None:
    """Git decides what Git accepts; the validator may not be the looser one."""
    assert not _git_accepts_ref(f"{stack_records.BASE_NAMESPACE}/{value}"), (
        "the corpus entry is one Git really refuses, so the assertion is not vacuous"
    )

    with pytest.raises(ValueError, match="ref"):
        stack_records.validate_ref_component(value)


@pytest.mark.parametrize("value", _GIT_ACCEPTS, ids=repr)
def test_every_name_git_accepts_is_returned_unchanged(value: str) -> None:
    """A branch Git would create must be recordable, so we are not stricter."""
    assert _git_accepts_ref(f"{stack_records.BASE_NAMESPACE}/{value}"), (
        "the corpus entry is one Git really accepts, so the assertion is not vacuous"
    )

    assert stack_records.validate_ref_component(value) == value, (
        "an acceptable name passes through unchanged, so callers can use it inline"
    )


def test_only_a_leading_dash_diverges_from_git_and_only_in_the_stricter_direction() -> (
    None
):
    """The one deliberate divergence is documented rather than accidental."""
    assert _git_accepts_ref(f"{stack_records.BASE_NAMESPACE}/-force"), (
        "Git stores a ref whose name begins with '-'; the danger is in the argv"
    )

    with pytest.raises(ValueError, match="argument injection"):
        stack_records.validate_ref_component("-force")


def test_the_namespace_constants_are_the_ones_the_documentation_promises() -> None:
    """The namespaces are a persisted interface, so they are pinned literally."""
    assert stack_records.BASE_NAMESPACE == "refs/stack-bases", "the anchor namespace"
    assert stack_records.TOMBSTONE_NAMESPACE == "refs/stack-tombstones", (
        "the tombstone namespace"
    )
    assert stack_records.RECORD_VERSION == "v1", "the first version of the format"
