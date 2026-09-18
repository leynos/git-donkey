"""Behaviour-driven tests for the stack record git donkey writes at birth.

The module binds scenarios from ``features/git_donkey_stack.feature`` to real
temporary repositories. It is the interoperability contract's first half: the
record a branch is born with is the record ``git wheresat`` will read and
``git plonk`` will entomb, so the assertions here are about the stored
artefacts — the four configuration keys and the anchor ref — rather than about
anything ``git donkey`` reports.

INV-11 is checked in both directions. A branch created from another feature
branch is recorded; a branch created from the trunk is not, and the second case
matters more than the first, because a record written for every branch would
offer a boundary to branches that never had a parent. The trunk case is
covered twice, once with the trunk named explicitly and once with the base left
to the workflow: an implicit base resolves to the fetched remote-tracking ref,
which is the same ref the decision compares against, so the two routes reach
the same answer by different comparisons.
"""

from __future__ import annotations

import typing as typ

from pytest_bdd import given, parsers, scenarios, then, when

from git_donkey import stack_records
from tests.integration.donkey_helpers import (
    DonkeyScenario,
    new_scenario,
    require_stack_record,
    run_donkey,
    seed_repo,
    stack_record,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest

# The branch every scenario's base is created from, and the commits it is at.
_TRUNK = "main"
_PARENT = "parent"

_UNTRACKED_KEYS = ("branch", "remote", "merge")


def _record_ids(scenario: DonkeyScenario, branch: str) -> tuple[str, str]:
    """Return the anchor ref's commit and the config's boundary for ``branch``."""
    record = require_stack_record(scenario, branch)
    anchor = scenario.repo.git.rev_parse(
        stack_records.base_ref_path(record.branch)
    ).strip()
    return anchor, record.base


@given("a repository whose trunk is main", target_fixture="scenario")
def repository_whose_trunk_is_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> DonkeyScenario:
    """Create a clone with a bare remote whose ``main`` is the trunk."""
    return new_scenario(tmp_path, monkeypatch, "child")


@given("a feature branch parent one commit ahead of main")
def feature_branch_ahead_of_main(scenario: DonkeyScenario) -> None:
    """Create ``parent`` from the trunk and commit to it.

    The commit is what makes the scenario about a stacked branch at all: a
    branch created at the trunk commit is not stacked, however it is named, so
    a parent still at the trunk would have no record to find (INV-11).
    """
    repo = scenario.repo
    repo.git.branch(_PARENT, _TRUNK)
    repo.git.checkout(_PARENT)
    seed_repo(repo, "parent.txt", "parent work")
    repo.git.checkout(_TRUNK)


@when(parsers.parse("I create a branch {branch} from {base} with git donkey"))
def create_branch_with_git_donkey(
    scenario: DonkeyScenario,
    capsys: pytest.CaptureFixture[str],
    branch: str,
    base: str,
) -> None:
    """Run ``git donkey`` for ``branch``, based explicitly on ``base``."""
    scenario.branch = branch
    run_donkey(scenario, capsys, base)


@when(parsers.parse("I create a branch {branch} with git donkey naming no base"))
def create_branch_from_the_default(
    scenario: DonkeyScenario,
    capsys: pytest.CaptureFixture[str],
    branch: str,
) -> None:
    """Run ``git donkey`` for ``branch`` against the remote's default branch.

    The base is not named, so the workflow selects it: the base ref becomes the
    fetched ``refs/remotes/<remote>/<default>``, which is also the trunk the
    recorded decision compares against. This is the invocation the other
    scenarios do not cover, and the only one where the base a user never named
    decides whether a record is written.

    The step is worded apart from ``... from {base} with git donkey`` rather
    than sharing a pattern that leaves the base implicit. ``parsers.parse``
    matches a step name with a greedy ``{branch}`` and ``fullmatch``, so
    "I create a branch {branch} with git donkey" also accepts the explicit
    step's text and captures ``child from parent`` as the branch name. Two
    steps that both accept one piece of Gherkin do not split it tidily; the
    ambiguity is resolved by the wording, not by the pattern.
    """
    scenario.branch = branch
    run_donkey(scenario, capsys)


@then(
    parsers.parse(
        "the branch configuration for {branch} names {parent} as its stack parent"
    )
)
def branch_configuration_names_the_parent(
    scenario: DonkeyScenario,
    branch: str,
    parent: str,
) -> None:
    """Check that the record's parent names the branch it was created from."""
    record = require_stack_record(scenario, branch)

    assert record.parent.branch == parent, (
        f"expected {branch!r} to record {parent!r} as its parent, got {record.parent!r}"
    )
    assert record.parent.pull_request is None, (
        "a record written at birth names the parent branch, not a pull request"
    )


@then(parsers.parse("the stack-base anchor for {branch} names the tip {parent} had"))
def anchor_names_the_parent_tip(
    scenario: DonkeyScenario,
    branch: str,
    parent: str,
) -> None:
    """Check that the anchor ref names the commit the new branch was cut from."""
    anchor, _base = _record_ids(scenario, branch)
    parent_tip = scenario.repo.heads[parent].commit.hexsha

    assert anchor == parent_tip, (
        f"expected the anchor for {branch!r} to name the tip {parent!r} had, "
        f"{parent_tip}, but it names {anchor}"
    )


@then("the recorded base equals the commit git donkey froze")
def recorded_base_is_the_frozen_commit(scenario: DonkeyScenario) -> None:
    """Check that the boundary is the commit the worktree was started from.

    That is the one commit git donkey resolved and then passed to
    ``git worktree add``, so the branch and its record name the same root. A
    record naming anything else would be evidence about a different branch.
    """
    anchor, base = _record_ids(scenario, scenario.branch)

    assert base == anchor, (
        f"expected the recorded boundary to agree with the anchor for "
        f"{scenario.branch!r}, got {base} and {anchor}"
    )
    assert base == scenario.worktree_head(), (
        f"expected the recorded boundary to be the commit {scenario.branch!r} "
        f"was created at, {scenario.worktree_head()}, got {base}"
    )


@then(parsers.parse("no stack record exists for {branch}"))
def no_stack_record_exists(scenario: DonkeyScenario, branch: str) -> None:
    """Check that a branch created from the trunk is not recorded as stacked."""
    assert branch in scenario.repo.heads, (
        f"expected {branch!r} to exist, so the assertion below is not vacuous"
    )

    record = stack_record(scenario, branch)

    assert record == stack_records.RecordAbsent(), (
        f"expected no record for the trunk-based branch {branch!r}, got {record!r}"
    )


@then(parsers.parse("{branch} has no upstream tracking configuration"))
def branch_has_no_upstream(scenario: DonkeyScenario, branch: str) -> None:
    """Check that recording a parent did not start tracking it."""
    # An unmatched --get-regexp is exit 1 with no output, so the query is
    # issued with ``with_exceptions=False`` and an empty result stands for a
    # branch with no section at all. It goes through dynamic dispatch because
    # GitPython's overloads for ``execute`` do not carry that keyword, and
    # ``git config`` is how the store reads configuration too.
    section = scenario.repo.git.config(
        "--local",
        "--get-regexp",
        f"^branch[.]{branch}[.]",
        with_exceptions=False,
    )
    keys = {entry.split()[0].rpartition(".")[2] for entry in section.splitlines()}

    assert not keys & set(_UNTRACKED_KEYS), (
        f"expected {branch!r} to track nothing, but its configuration names "
        f"{sorted(keys & set(_UNTRACKED_KEYS))}"
    )


scenarios("features/git_donkey_stack.feature")
