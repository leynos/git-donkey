"""Behaviour-driven tests for the record lifecycle ``git plonk`` owns.

The module binds scenarios from ``features/git_plonk_stack.feature`` to real
temporary repositories and asserts against the artefacts a sweep leaves behind:
the tombstone ref, the anchor ref, the branch configuration, and the summary.
It is the interoperability contract's second half — the record ``git donkey``
writes at birth is the one ``git plonk`` entombs at death, and the one
``git wheresat`` will later read — so the fixtures stack branches rather than
only creating them, and a branch cut from the trunk is exercised beside one cut
from another branch.

Two deletions that look alike are deliberately kept apart. ``git update-ref -d``
removes a branch ref alone, so its record outlives it and the sweep can convert
that record into a tombstone; plain ``git branch -D`` destroys the whole
``branch.<name>`` section, so the sweep can only clear what is left. A summary
that named both alike would claim a rescue that never happened, which is why the
last scenario asserts the section the cleared orphan lands in.
"""

from __future__ import annotations

import typing as typ

from pytest_bdd import given, scenarios, then, when

from git_donkey import plonk, stack_records, stack_store
from tests.integration.plonk_helpers import (
    PlonkStackScenario,
    aged_tombstone,
    create_stacked_git_donkey_worktree,
    orphan_by_branch_deletion,
    orphan_by_ref_deletion,
    stacked_completed_worktree,
    trunk_completed_worktree,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest

# The branches each scenario builds, named apart so a failure points at the
# scenario that seeded it. The parent is one commit ahead of the trunk wherever
# it appears: a branch created at the trunk commit is not stacked, however it is
# named, and INV-11 records nothing for it.
_PARENT = "issue-150-parent"
_STACKED = "issue-151-stacked"

_TRUNK_BRANCH = "issue-160-parent"
_CHILD = "issue-161-child"

_ORPHAN_PARENT = "issue-180-parent"
_ORPHAN = "issue-181-orphaned"

_PLAIN_ORPHAN_PARENT = "issue-190-parent"
_PLAIN_ORPHAN = "issue-191-orphaned"

_STALE_TOMBSTONE = "issue-200-stale"


def _rescuable(scenario: PlonkStackScenario) -> tuple[str, ...]:
    """Return the orphans of ``scenario`` whose record still carries a tip."""
    assert scenario.orphan is not None, "expected the scenario to name an orphan"
    reader = stack_store.GitStackRecordReader(scenario.repo)
    return reader.rescuable((scenario.orphan,))


def _assert_reported(output: str, heading: str, entry: str) -> None:
    """Assert ``output`` reports ``entry`` under ``heading``.

    The entry is matched as a whole bullet, and only among the bullets of the
    heading's own section. The scan starts after the line the heading was found
    on, because the remainder of that line is the heading's own tail rather
    than a bullet of the section, and it ends at the first line that is not a
    bullet — a blank line included, so the bullets of one section have to be
    consecutive for the section to be read as one. A branch that shares a prefix
    with another is therefore not read as the one named, and neither is a branch
    the summary reports under a different heading.
    """
    assert heading in output, f"expected {heading!r} in the git plonk summary"
    remainder = output.partition(heading)[2].splitlines()
    bullets: list[str] = []
    for line in remainder[1:]:
        if not line.startswith("- "):
            break
        bullets.append(line)
    assert f"- {entry}" in bullets, (
        f"expected the summary to report {entry!r} under {heading!r}"
    )


@given(
    "a completed git donkey worktree for a branch with a stack record",
    target_fixture="scenario",
)
def completed_worktree_with_stack_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkStackScenario:
    """Create a completed worktree for a branch stacked on another branch."""
    scenario = stacked_completed_worktree(
        tmp_path,
        monkeypatch,
        parent=_PARENT,
        branch=_STACKED,
    )

    assert isinstance(scenario.record, stack_records.StackRecord), (
        "expected the branch to have been born with a record, since its base "
        f"was not the trunk, got {scenario.record!r}"
    )
    assert scenario.record.parent.branch == _PARENT, (
        "expected the record to name the branch the worktree was cut from"
    )
    return scenario


@given(
    "a completed git donkey worktree for a branch created from the trunk",
    target_fixture="scenario",
)
def completed_worktree_from_the_trunk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkStackScenario:
    """Create a completed worktree for a branch that has no record of its own."""
    scenario = trunk_completed_worktree(
        tmp_path,
        monkeypatch,
        branch=_TRUNK_BRANCH,
    )

    assert scenario.record == stack_records.RecordAbsent(), (
        "expected a branch created from the trunk to have no record, which is "
        f"the case this scenario is about, got {scenario.record!r}"
    )
    return scenario


@given("a child branch stacked on it", target_fixture="scenario")
def child_branch_stacked_on_it(
    scenario: PlonkStackScenario,
) -> PlonkStackScenario:
    """Stack a child on the completed branch, and keep the parent's record.

    The child is created after the parent's worktree was committed to, so the
    boundary it records is the tip the parent reached — the one commit a
    surviving child cannot find anywhere once the parent's branch is gone.
    Creating it moves nothing, so the scenario's captured tip still stands.

    Parameters
    ----------
    scenario : PlonkStackScenario
        Scenario holding the completed branch the child is stacked on.

    Returns
    -------
    PlonkStackScenario
        The same scenario, now with a child branch stacked on its parent.

    """
    create_stacked_git_donkey_worktree(
        scenario.local_path, _CHILD, scenario.completed_branch
    )
    return scenario


@given(
    "a stack record whose branch was deleted outside git plonk",
    target_fixture="scenario",
)
def record_whose_branch_was_deleted_outside_plonk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkStackScenario:
    """Delete a recorded branch's ref alone, leaving its record behind."""
    scenario = orphan_by_ref_deletion(
        tmp_path,
        monkeypatch,
        branch=_ORPHAN,
        parent=_ORPHAN_PARENT,
    )

    assert _rescuable(scenario) == (scenario.orphan,), (
        "expected the orphaned record to still carry a tip: removing the branch "
        "ref alone is what makes a deleted branch rescuable"
    )
    return scenario


@given(
    "a stack record whose branch was deleted with plain git",
    target_fixture="scenario",
)
def record_whose_branch_was_deleted_with_plain_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkStackScenario:
    """Delete a recorded branch through Git alone, as a user would."""
    scenario = orphan_by_branch_deletion(
        tmp_path,
        monkeypatch,
        branch=_PLAIN_ORPHAN,
        parent=_PLAIN_ORPHAN_PARENT,
    )

    assert not _rescuable(scenario), (
        "expected git branch -D to have taken the record's tip with the branch, "
        "leaving the sweep with nothing to preserve for this orphan"
    )
    return scenario


@given("a tombstone older than the retention window", target_fixture="scenario")
def tombstone_older_than_the_retention_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkStackScenario:
    """Create a tombstone whose reflog entry predates the configured window."""
    scenario = aged_tombstone(tmp_path, monkeypatch, branch=_STALE_TOMBSTONE)
    reader = stack_store.GitStackRecordReader(scenario.repo)

    assert reader.expired(stack_records.DEFAULT_TOMBSTONE_EXPIRE) == (
        _STALE_TOMBSTONE,
    ), (
        "expected the tombstone to be older than the window a run will apply, "
        "so the prune below is not vacuous"
    )
    return scenario


@when("I run git plonk in hard mode", target_fixture="plonk_output")
def run_hard_plonk(
    scenario: PlonkStackScenario,
    capsys: pytest.CaptureFixture[str],
) -> str:
    """Run the hard cleanup mode and capture its report."""
    exit_code = plonk.run_git_plonk(hard=True)

    assert exit_code == 0, "expected hard git plonk to succeed"
    return capsys.readouterr().out


@when("I run git plonk in hard mode as a dry run", target_fixture="plonk_output")
def run_hard_dry_run_plonk(
    scenario: PlonkStackScenario,
    capsys: pytest.CaptureFixture[str],
) -> str:
    """Run the hard cleanup mode as a preview and capture its report."""
    exit_code = plonk.run_git_plonk(hard=True, dry_run=True)

    assert exit_code == 0, "expected hard dry-run git plonk to succeed"
    return capsys.readouterr().out


@when("I run git plonk in default mode", target_fixture="plonk_output")
def run_default_plonk(
    scenario: PlonkStackScenario,
    capsys: pytest.CaptureFixture[str],
) -> str:
    """Run the default cleanup mode and capture its report."""
    exit_code = plonk.run_git_plonk()

    assert exit_code == 0, "expected default git plonk to succeed"
    return capsys.readouterr().out


@then("the branch is deleted")
def the_branch_is_deleted(scenario: PlonkStackScenario) -> None:
    """Assert the completed branch is gone, so the tombstone is not the branch."""
    assert scenario.completed_branch not in scenario.repo.heads, (
        f"expected {scenario.completed_branch!r} to be deleted by the sweep"
    )


@then("a tombstone names the tip the branch had")
def a_tombstone_names_the_tip(scenario: PlonkStackScenario) -> None:
    """Assert the tombstone names the commit the branch was at when it went."""
    assert scenario.tip is not None, "expected the scenario to have captured a tip"
    assert scenario.tombstone(scenario.completed_branch) == scenario.tip, (
        f"expected the tombstone for {scenario.completed_branch!r} to name "
        f"{scenario.tip}, the commit the branch held before the run"
    )


@then("no live stack record remains for that branch")
def no_live_record_remains(scenario: PlonkStackScenario) -> None:
    """Assert the entombed branch holds neither artefact of a live record."""
    reader = stack_store.GitStackRecordReader(scenario.repo)

    assert scenario.anchor(scenario.completed_branch) is None, (
        "expected the stack-base anchor to be removed with the record, so no "
        "record namespace outlives the branch namespace it mirrors (INV-9)"
    )
    assert reader.read(scenario.completed_branch) == stack_records.RecordAbsent(), (
        "expected the branch to hold no record once its tip is a tombstone"
    )


@then("the summary names the tombstone it would write")
def summary_names_the_planned_tombstone(
    scenario: PlonkStackScenario,
    plonk_output: str,
) -> None:
    """Assert a dry run reports the entombment it did not perform."""
    _assert_reported(
        plonk_output,
        "Planned branch entombments:",
        scenario.completed_branch,
    )


@then("no tombstone is created")
def no_tombstone_is_created(scenario: PlonkStackScenario) -> None:
    """Assert the branch the run named is not entombed by a plan or a clear."""
    assert scenario.tombstone(scenario.completed_branch) is None, (
        f"expected no tombstone for {scenario.completed_branch!r}"
    )


@then("the stack record is unchanged")
def the_stack_record_is_unchanged(scenario: PlonkStackScenario) -> None:
    """Assert a dry run left the record it read exactly as it found it."""
    assert scenario.record is not None, (
        "expected the scenario to have captured the record before the run"
    )
    reader = stack_store.GitStackRecordReader(scenario.repo)

    assert reader.read(scenario.completed_branch) == scenario.record, (
        "expected the dry run to leave the stack record untouched"
    )
    assert scenario.anchor(scenario.completed_branch) is not None, (
        "expected the dry run to leave the stack-base anchor in place too"
    )


@then("the orphaned record becomes a tombstone")
def the_orphaned_record_becomes_a_tombstone(scenario: PlonkStackScenario) -> None:
    """Assert the sweep preserved the tip the orphaned record still carried."""
    assert scenario.orphan is not None, "expected the scenario to name an orphan"

    assert scenario.tombstone(scenario.orphan) == scenario.tip, (
        f"expected the tombstone for {scenario.orphan!r} to name "
        f"{scenario.tip}, the tip its record carried"
    )


@then("the summary names the record it swept")
def summary_names_the_swept_record(
    scenario: PlonkStackScenario,
    plonk_output: str,
) -> None:
    """Assert the swept orphan is reported as the rescue it was."""
    assert scenario.orphan is not None, "expected the scenario to name an orphan"
    _assert_reported(
        plonk_output,
        "Swept records (tip preserved):",
        scenario.orphan,
    )


@then("the orphaned record is cleared")
def the_orphaned_record_is_cleared(scenario: PlonkStackScenario) -> None:
    """Assert the cleared orphan holds neither artefact of a record."""
    assert scenario.orphan is not None, "expected the scenario to name an orphan"
    reader = stack_store.GitStackRecordReader(scenario.repo)

    assert scenario.anchor(scenario.orphan) is None, (
        "expected the anchor of a cleared orphan to be removed, so nothing is "
        "left standing in the record namespace (INV-9)"
    )
    assert reader.read(scenario.orphan) == stack_records.RecordAbsent(), (
        "expected the cleared orphan to hold no record"
    )


@then("the summary reports the orphan as having no tip to preserve")
def summary_reports_the_orphan_with_no_tip(
    scenario: PlonkStackScenario,
    plonk_output: str,
) -> None:
    """Assert the orphan Git deleted alone is not reported as a rescue."""
    assert scenario.orphan is not None, "expected the scenario to name an orphan"
    _assert_reported(
        plonk_output,
        "Swept records (no tip to preserve):",
        scenario.orphan,
    )
    assert "Swept records (tip preserved):" not in plonk_output, (
        "expected an orphan with no tip left to be reported apart from a rescue"
    )


@then("the tombstone is deleted")
def the_tombstone_is_deleted(scenario: PlonkStackScenario) -> None:
    """Assert the prune removed the tombstone its window reached."""
    assert scenario.tombstone(scenario.completed_branch) is None, (
        f"expected the tombstone for {scenario.completed_branch!r} to be pruned"
    )


@then("the summary names the tombstone it pruned")
def summary_names_the_pruned_tombstone(
    scenario: PlonkStackScenario,
    plonk_output: str,
) -> None:
    """Assert the prune names both the tombstone it took and the window it used."""
    _assert_reported(
        plonk_output,
        f"Pruned tombstones (older than {stack_records.DEFAULT_TOMBSTONE_EXPIRE}):",
        scenario.completed_branch,
    )


scenarios("features/git_plonk_stack.feature")
