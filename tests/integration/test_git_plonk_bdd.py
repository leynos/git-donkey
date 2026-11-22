"""Behaviour-driven integration tests for the ``git plonk`` command.

The module binds scenarios from ``features/git_plonk.feature`` to real
temporary Git repositories created by ``tests.integration.conftest._setup_repo``
and seeded through ``tests.integration.plonk_helpers``. The steps assert on
branches, worktree paths, generated directories, and exit codes.

These tests exercise the public ``git_donkey.plonk.run_git_plonk`` workflow and
the ``git_donkey.cli`` command boundary rather than low-level helpers. They
validate default, soft, hard, and mutually-exclusive flag behaviour, and the
skip-and-report contract for completed worktrees holding a tracked
modification, a staged change, or an untracked file. The history the modes
treat as trunk is covered separately in ``test_git_plonk_trunk_history.py``.
"""

from __future__ import annotations

import typing as typ

import pytest
from git import Repo
from pytest_bdd import given, parsers, scenarios, then, when

from git_donkey import cli, plonk
from tests.integration.conftest import _setup_repo
from tests.integration.plonk_helpers import (
    MODIFIED_CONTENT,
    STAGED_CONTENT,
    TRACKED_FILE,
    PlonkScenario,
    commit_completion_marker,
    commit_ignore_rule,
    create_git_donkey_worktree,
    edit_tracked_file,
    record_plonk_run,
    skipped_entry,
    stage_tracked_change,
)
from tests.output_assertions import require_stream, stream

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

# Cyclopts exits with this code when mutually exclusive flags are supplied.
_USAGE_ERROR_EXIT_CODE = 2

# Each cleanup mode the feature names, mapped to the call that runs it. The
# dry runs go through the CLI boundary so flag parsing is covered as well.
_PLONK_INVOCATIONS: dict[str, cabc.Callable[[], int | None]] = {
    "default": plonk.run_git_plonk,
    "default dry-run": lambda: cli._plonk_app(["--dry-run"]),
    "soft": lambda: plonk.run_git_plonk(soft=True),
    "soft dry-run": lambda: plonk.run_git_plonk(soft=True, dry_run=True),
    "hard": lambda: plonk.run_git_plonk(hard=True),
    "hard dry-run": lambda: plonk.run_git_plonk(hard=True, dry_run=True),
}


@given(
    "a repository with completed and active git donkey worktrees",
    target_fixture="scenario",
)
def repository_with_completed_and_active_worktrees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Create one completed issue worktree and one active issue worktree."""
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    completed_branch = "issue-123-fix-closed-work"
    active_branch = "issue-456-active-work"

    create_git_donkey_worktree(local_path, completed_branch)
    create_git_donkey_worktree(local_path, active_branch)
    commit_completion_marker(local_path, "(#123)")

    return PlonkScenario(
        local_path=local_path,
        completed_branch=completed_branch,
        active_branch=active_branch,
    )


@given(
    "a repository with generated directories inside git donkey worktrees",
    target_fixture="scenario",
)
def repository_with_generated_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Create worktrees containing generated dependency and build directories."""
    scenario = repository_with_completed_and_active_worktrees(tmp_path, monkeypatch)
    assert scenario.active_branch is not None, "expected active branch in scenario"
    for branch_name in (scenario.completed_branch, scenario.active_branch):
        worktree_path = scenario.worktree_path(branch_name)
        for dirname in ("target", "node_modules"):
            generated_path = worktree_path / dirname
            generated_path.mkdir()
            (generated_path / "generated.txt").write_text("generated")
    return scenario


@given("a repository with a completed git donkey worktree", target_fixture="scenario")
def repository_with_completed_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Create a single completed roadmap worktree."""
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    completed_branch = "road-1-2-3a-4-finished-task"

    create_git_donkey_worktree(local_path, completed_branch)
    commit_completion_marker(local_path, "(road.1.2.3a.4)")

    return PlonkScenario(local_path=local_path, completed_branch=completed_branch)


@given(
    "a repository with a completed git donkey worktree holding a staged change "
    "beside a clean one",
    target_fixture="scenario",
)
def repository_with_staged_change_and_clean_completed_worktrees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Create one completed worktree holding a staged change and a clean sibling."""
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    clean_branch = "issue-123-fix-closed-work"
    dirty_branch = "issue-124-fix-uncommitted-work"

    create_git_donkey_worktree(local_path, clean_branch)
    create_git_donkey_worktree(local_path, dirty_branch)
    commit_completion_marker(local_path, "(#123)")
    commit_completion_marker(local_path, "(#124)")
    scenario = PlonkScenario(
        local_path=local_path,
        completed_branch=clean_branch,
        dirty_branch=dirty_branch,
    )
    stage_tracked_change(scenario, dirty_branch, STAGED_CONTENT)
    return scenario


@given(
    "a repository with a completed git donkey worktree holding a tracked modification",
    target_fixture="scenario",
)
def repository_with_tracked_modification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Create a completed worktree holding an uncommitted edit to a tracked file."""
    scenario = repository_with_completed_worktree(tmp_path, monkeypatch)
    edit_tracked_file(scenario, scenario.completed_branch, MODIFIED_CONTENT)
    return scenario


@given(
    "a repository with a completed git donkey worktree holding an untracked file",
    target_fixture="scenario",
)
def repository_with_untracked_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Create a completed worktree whose only extra content is untracked."""
    scenario = repository_with_completed_worktree(tmp_path, monkeypatch)
    uncommitted = scenario.worktree_path(scenario.completed_branch) / "scratch.txt"
    uncommitted.write_text("work in progress")
    return scenario


@given(
    "a repository with a completed git donkey worktree holding ignored build output",
    target_fixture="scenario",
)
def repository_with_ignored_build_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> PlonkScenario:
    """Create a completed worktree whose only extra content is ignored output."""
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    commit_ignore_rule(local_path, "build/")
    completed_branch = "issue-123-fix-closed-work"

    create_git_donkey_worktree(local_path, completed_branch)
    commit_completion_marker(local_path, "(#123)")
    scenario = PlonkScenario(local_path=local_path, completed_branch=completed_branch)
    build = scenario.worktree_path(completed_branch) / "build"
    build.mkdir()
    (build / "artifact.bin").write_bytes(b"artifact")
    return scenario


def _require_report(scenario: PlonkScenario, *lines: str) -> None:
    """Assert the run printed exactly ``lines``, each terminated by a newline."""
    require_stream(scenario.stdout, stream(*lines), context="git plonk stdout")


@when(parsers.parse("I run git plonk in {mode} mode"))
def run_plonk(
    scenario: PlonkScenario,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    """Run the cleanup mode the step names and record its report."""
    record_plonk_run(scenario, capsys, mode, _PLONK_INVOCATIONS[mode])


@when("I run git plonk with soft and hard modes", target_fixture="plonk_exit")
def run_conflicting_plonk_modes(scenario: PlonkScenario) -> int | str | None:
    """Run the CLI boundary with mutually exclusive cleanup modes."""
    with pytest.raises(SystemExit) as exc_info:
        cli._plonk_app(["--soft", "--hard"])
    return exc_info.value.code


@then("the completed worktree is removed")
def completed_worktree_is_removed(scenario: PlonkScenario) -> None:
    """Assert the completed worktree path no longer exists."""
    assert not scenario.worktree_path(scenario.completed_branch).exists(), (
        "expected completed worktree to be removed"
    )


@then("the completed worktree remains")
def completed_worktree_remains(scenario: PlonkScenario) -> None:
    """Assert the completed worktree path still exists."""
    assert scenario.worktree_path(scenario.completed_branch).exists(), (
        "expected completed worktree to remain"
    )


@then("the dirty completed worktree remains")
def dirty_completed_worktree_remains(scenario: PlonkScenario) -> None:
    """Assert the dirty completed worktree, and the work inside it, survive."""
    assert scenario.dirty_branch is not None, "expected dirty branch in scenario"
    worktree_path = scenario.worktree_path(scenario.dirty_branch)
    assert worktree_path.exists(), "expected dirty completed worktree to remain"
    staged = worktree_path / TRACKED_FILE
    assert staged.read_text() == STAGED_CONTENT, (
        "expected the staged change to survive the sweep"
    )
    assert TRACKED_FILE in Repo(worktree_path).git.diff("--cached", "--name-only"), (
        "expected the change to remain staged, not merely present in the file"
    )


@then("git plonk reports the dirty worktree as skipped")
def git_plonk_reports_dirty_worktree_skipped(scenario: PlonkScenario) -> None:
    """Assert the report removes the clean worktree and names the dirty one."""
    assert scenario.dirty_branch is not None, "expected dirty branch in scenario"
    _require_report(
        scenario,
        f"git-plonk: mode={scenario.mode}",
        "Removed worktrees:",
        f"- {scenario.worktree_path(scenario.completed_branch)}",
        "Skipped worktrees:",
        skipped_entry(
            scenario.worktree_path(scenario.dirty_branch),
            "uncommitted changes",
        ),
    )


@then("git plonk reports the completed worktree as skipped")
def git_plonk_reports_completed_worktree_skipped(scenario: PlonkScenario) -> None:
    """Assert the report names the completed worktree and why it survived."""
    _require_report(
        scenario,
        f"git-plonk: mode={scenario.mode}",
        "Skipped worktrees:",
        skipped_entry(
            scenario.worktree_path(scenario.completed_branch),
            "uncommitted changes",
        ),
    )


@then("the active worktree remains")
def active_worktree_remains(scenario: PlonkScenario) -> None:
    """Assert the active worktree path remains."""
    assert scenario.active_branch is not None, "expected active branch in scenario"
    assert scenario.worktree_path(scenario.active_branch).exists(), (
        "expected active worktree to remain"
    )


@then("the completed branch remains")
def completed_branch_remains(scenario: PlonkScenario) -> None:
    """Assert default mode leaves the completed local branch intact."""
    assert scenario.completed_branch in Repo(scenario.local_path).heads, (
        "expected default mode to keep completed branch"
    )


def _scenario_branches(scenario: PlonkScenario) -> tuple[str, str]:
    """Return the completed and active branch names from ``scenario``."""
    assert scenario.active_branch is not None, "expected active branch in scenario"
    return scenario.completed_branch, scenario.active_branch


def _assert_generated_directories(scenario: PlonkScenario, *, exist: bool) -> None:
    """Assert generated worktree directories exist or are absent for all branches."""
    for branch_name in _scenario_branches(scenario):
        worktree_path = scenario.worktree_path(branch_name)
        for name in ("target", "node_modules"):
            path = worktree_path / name
            message = (
                f"expected {name} to {'remain in' if exist else 'be removed from'} "
                f"{branch_name}"
            )
            if exist:
                assert path.is_dir(), message
            else:
                assert not path.exists(), message


@then("the generated directories are removed")
def generated_directories_are_removed(scenario: PlonkScenario) -> None:
    """Assert generated directories are removed from every git-donkey worktree."""
    _assert_generated_directories(scenario, exist=False)


@then("the generated directories remain")
def generated_directories_remain(scenario: PlonkScenario) -> None:
    """Assert generated directories remain in every git-donkey worktree."""
    _assert_generated_directories(scenario, exist=True)


@then("the worktrees remain")
def worktrees_remain(scenario: PlonkScenario) -> None:
    """Assert soft mode leaves every linked worktree in place."""
    assert scenario.active_branch is not None, "expected active branch in scenario"
    for branch_name in (scenario.completed_branch, scenario.active_branch):
        assert scenario.worktree_path(branch_name).exists(), (
            f"expected soft mode to keep worktree {branch_name}"
        )


@then("the branches remain")
def branches_remain(scenario: PlonkScenario) -> None:
    """Assert soft mode leaves every local branch in place."""
    assert scenario.active_branch is not None, "expected active branch in scenario"
    heads = Repo(scenario.local_path).heads
    assert scenario.completed_branch in heads, (
        "expected soft mode to keep completed branch"
    )
    assert scenario.active_branch in heads, "expected soft mode to keep active branch"


@then("the completed branch is deleted")
def completed_branch_is_deleted(scenario: PlonkScenario) -> None:
    """Assert hard mode deletes the completed local branch."""
    assert scenario.completed_branch not in Repo(scenario.local_path).heads, (
        "expected hard mode to delete completed branch"
    )


@then("git plonk exits with a usage error")
def git_plonk_exits_with_usage_error(plonk_exit: int | str | None) -> None:
    """Assert conflicting cleanup modes fail as a usage error."""
    assert plonk_exit == _USAGE_ERROR_EXIT_CODE, (
        "expected conflicting plonk modes to exit with a usage error"
    )


def _scenario_worktree_paths(scenario: PlonkScenario) -> list[Path]:
    """Return the scenario's worktrees in the order Git registers them."""
    wanted = {scenario.worktree_path(branch) for branch in _scenario_branches(scenario)}
    return [path for path in scenario.known_worktrees if path in wanted]


@then("git plonk reports planned worktree and branch cleanup")
def git_plonk_reports_planned_cleanup(scenario: PlonkScenario) -> None:
    """Assert dry-run output describes the work that would be performed."""
    _require_report(
        scenario,
        f"git-plonk: mode={scenario.mode}",
        "Planned worktree removals:",
        f"- {scenario.worktree_path(scenario.completed_branch)}",
        "Planned branch deletions:",
        f"- {scenario.completed_branch}",
    )


@then("git plonk reports planned default worktree cleanup only")
def git_plonk_reports_planned_default_cleanup(scenario: PlonkScenario) -> None:
    """Assert default dry-run output excludes hard-mode branch deletion."""
    _require_report(
        scenario,
        f"git-plonk: mode={scenario.mode}",
        "Planned worktree removals:",
        f"- {scenario.worktree_path(scenario.completed_branch)}",
    )


@then("git plonk reports planned generated path cleanup")
def git_plonk_reports_planned_generated_cleanup(scenario: PlonkScenario) -> None:
    """Assert soft dry-run output describes generated paths it would remove."""
    lines = [
        f"git-plonk: mode={scenario.mode}",
        "Planned generated path removals:",
    ]
    lines.extend(
        f"- {worktree_path / name}"
        for worktree_path in _scenario_worktree_paths(scenario)
        for name in ("target", "node_modules")
    )
    _require_report(scenario, *lines)


scenarios("features/git_plonk.feature")
