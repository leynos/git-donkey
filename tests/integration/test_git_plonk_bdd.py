"""Behaviour-driven integration tests for the ``git plonk`` command.

The module binds scenarios from ``features/git_plonk.feature`` to real
temporary Git repositories created by ``tests.integration.conftest._setup_repo``.
Shared helpers create `git donkey` worktrees, add trunk completion-marker
commits, and expose a small ``PlonkScenario`` object so BDD steps can assert on
branches, worktree paths, generated directories, and exit codes.

These tests exercise the public ``git_donkey.plonk.run_git_plonk`` workflow and
the ``git_donkey.cli`` command boundary rather than low-level helpers. They
validate default, soft, hard, and mutually-exclusive flag behavior, and include
direct regression tests proving that cleanup uses main/trunk history even when
invoked from a linked topic worktree.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from git import Repo
from pytest_bdd import given, scenarios, then, when

from git_donkey import cli, donkey, plonk
from tests.integration.conftest import _setup_repo


@dataclasses.dataclass(frozen=True)
class PlonkScenario:
    """Repository state shared by BDD steps."""

    local_path: Path
    completed_branch: str
    active_branch: str | None = None

    @property
    def worktree_root(self) -> Path:
        """Return the git-donkey worktree root for the scenario repository."""
        return self.local_path.parent / f"{self.local_path.name}.worktrees"

    def worktree_path(self, branch_name: str) -> Path:
        """Return the expected worktree path for ``branch_name``."""
        return self.worktree_root / branch_name


def _commit_completion_marker(local_path: Path, marker: str) -> None:
    """Commit a completion marker on ``main`` without changing worktree content."""
    repo = Repo(local_path)
    repo.git.checkout("main")
    marker_name = marker.removeprefix("(").removesuffix(")").replace("#", "issue-")
    marker_path = local_path / f"completion-{marker_name}.txt"
    marker_path.write_text(marker)
    repo.index.add([marker_path.as_posix()])
    repo.index.commit(f"Complete work {marker}")
    repo.remote("origin").push("main")


def _create_git_donkey_worktree(local_path: Path, branch_name: str) -> None:
    """Create a git-donkey worktree in ``local_path`` for ``branch_name``."""
    repo = Repo(local_path)
    repo.git.checkout("main")
    exit_code = donkey.run_git_donkey(branch_name, no_pull=True)
    assert exit_code == 0, f"expected git donkey to create {branch_name}"


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

    _create_git_donkey_worktree(local_path, completed_branch)
    _create_git_donkey_worktree(local_path, active_branch)
    _commit_completion_marker(local_path, "(#123)")

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

    _create_git_donkey_worktree(local_path, completed_branch)
    _commit_completion_marker(local_path, "(road.1.2.3a.4)")

    return PlonkScenario(local_path=local_path, completed_branch=completed_branch)


@when("I run git plonk in default mode")
def run_default_plonk(scenario: PlonkScenario) -> None:
    """Run the default cleanup mode."""
    exit_code = plonk.run_git_plonk()
    assert exit_code == 0, "expected default git plonk to succeed"


@when("I run git plonk in default dry-run mode", target_fixture="plonk_output")
def run_default_dry_run_plonk(
    scenario: PlonkScenario,
    capsys: pytest.CaptureFixture[str],
) -> str:
    """Run default cleanup in dry-run mode through the CLI boundary."""
    with pytest.raises(SystemExit) as exc_info:
        cli._plonk_app(["--dry-run"])
    assert exc_info.value.code == 0, "expected default dry-run git plonk to succeed"
    return capsys.readouterr().out


@when("I run git plonk in soft mode")
def run_soft_plonk(scenario: PlonkScenario) -> None:
    """Run the soft cleanup mode."""
    exit_code = plonk.run_git_plonk(soft=True)
    assert exit_code == 0, "expected soft git plonk to succeed"


@when("I run git plonk in soft dry-run mode", target_fixture="plonk_output")
def run_soft_dry_run_plonk(
    scenario: PlonkScenario,
    capsys: pytest.CaptureFixture[str],
) -> str:
    """Run soft cleanup in dry-run mode and capture its report."""
    exit_code = plonk.run_git_plonk(soft=True, dry_run=True)
    assert exit_code == 0, "expected soft dry-run git plonk to succeed"
    return capsys.readouterr().out


@when("I run git plonk in hard mode")
def run_hard_plonk(scenario: PlonkScenario) -> None:
    """Run the hard cleanup mode."""
    exit_code = plonk.run_git_plonk(hard=True)
    assert exit_code == 0, "expected hard git plonk to succeed"


@when("I run git plonk in hard dry-run mode", target_fixture="plonk_output")
def run_hard_dry_run_plonk(
    scenario: PlonkScenario,
    capsys: pytest.CaptureFixture[str],
) -> str:
    """Run hard cleanup in dry-run mode and capture its report."""
    exit_code = plonk.run_git_plonk(hard=True, dry_run=True)
    assert exit_code == 0, "expected hard dry-run git plonk to succeed"
    return capsys.readouterr().out


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
    assert scenario.active_branch is not None, "expected active branch in scenario"
    return scenario.completed_branch, scenario.active_branch


def _assert_generated_directories(scenario: PlonkScenario, *, exist: bool) -> None:
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
    assert plonk_exit == 2, "expected conflicting plonk modes to exit with code 2"


def _assert_dry_run_header(mode: str, plonk_output: str) -> None:
    message = (
        "expected dry-run summary header"
        if mode == "hard"
        else f"expected {mode} dry-run summary header"
    )
    assert f"git-plonk: mode={mode} dry-run" in plonk_output, message


def _assert_planned_worktree_removal(
    scenario: PlonkScenario,
    plonk_output: str,
) -> None:
    assert "Planned worktree removals:" in plonk_output, (
        "expected planned worktree section"
    )
    assert str(scenario.worktree_path(scenario.completed_branch)) in plonk_output, (
        "expected completed worktree path in dry-run output"
    )


def _assert_branch_deletion_section(
    scenario: PlonkScenario,
    plonk_output: str,
    *,
    expected: bool,
) -> None:
    if expected:
        assert "Planned branch deletions:" in plonk_output, (
            "expected planned branch section"
        )
        assert f"- {scenario.completed_branch}" in plonk_output, (
            "expected completed branch in dry-run output"
        )
    else:
        assert "Planned branch deletions:" not in plonk_output, (
            "expected default dry-run output to omit branch deletion section"
        )
        assert f"- {scenario.completed_branch}" not in plonk_output, (
            "expected default dry-run output to omit completed branch deletion"
        )


@then("git plonk reports planned worktree and branch cleanup")
def git_plonk_reports_planned_cleanup(
    scenario: PlonkScenario,
    plonk_output: str,
) -> None:
    """Assert dry-run output describes the work that would be performed."""
    _assert_dry_run_header("hard", plonk_output)
    _assert_planned_worktree_removal(scenario, plonk_output)
    _assert_branch_deletion_section(scenario, plonk_output, expected=True)


@then("git plonk reports planned default worktree cleanup only")
def git_plonk_reports_planned_default_cleanup(
    scenario: PlonkScenario,
    plonk_output: str,
) -> None:
    """Assert default dry-run output excludes hard-mode branch deletion."""
    _assert_dry_run_header("default", plonk_output)
    _assert_planned_worktree_removal(scenario, plonk_output)
    _assert_branch_deletion_section(scenario, plonk_output, expected=False)


@then("git plonk reports planned generated path cleanup")
def git_plonk_reports_planned_generated_cleanup(
    scenario: PlonkScenario,
    plonk_output: str,
) -> None:
    """Assert soft dry-run output describes generated paths it would remove."""
    branches = _scenario_branches(scenario)
    _assert_dry_run_header("soft", plonk_output)
    assert "Planned generated path removals:" in plonk_output, (
        "expected planned generated path section"
    )
    for branch_name in branches:
        worktree_path = scenario.worktree_path(branch_name)
        assert str(worktree_path / "target") in plonk_output, (
            f"expected target path in dry-run output for {branch_name}"
        )
        assert str(worktree_path / "node_modules") in plonk_output, (
            f"expected node_modules path in dry-run output for {branch_name}"
        )


def test_git_plonk_uses_main_history_when_run_from_topic_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default mode should ignore topic-only markers from a topic CWD."""
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    completed_branch = "issue-789-completed-from-main"
    topic_branch = "issue-790-active-topic"

    _create_git_donkey_worktree(local_path, completed_branch)
    _create_git_donkey_worktree(local_path, topic_branch)
    scenario = PlonkScenario(
        local_path=local_path,
        completed_branch=completed_branch,
        active_branch=topic_branch,
    )

    monkeypatch.chdir(scenario.worktree_path(topic_branch))
    topic_repo = Repo(Path.cwd())
    marker_path = Path.cwd() / "topic-only-marker.txt"
    marker_path.write_text("(#789)")
    topic_repo.index.add([marker_path.as_posix()])
    topic_repo.index.commit("Topic-only completion marker (#789)")
    exit_code = plonk.run_git_plonk()

    assert exit_code == 0, "expected default git plonk to succeed from topic worktree"
    assert scenario.worktree_path(completed_branch).exists(), (
        "expected trunk history to ignore topic-only completion marker"
    )
    assert scenario.worktree_path(topic_branch).exists(), (
        "expected topic worktree to remain"
    )
    assert completed_branch in Repo(local_path).heads, (
        "expected completed branch to remain without trunk marker"
    )


def test_git_plonk_removes_main_completed_worktree_from_topic_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default mode should use main markers when run from a topic CWD."""
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    completed_branch = "issue-791-completed-on-main"
    topic_branch = "issue-792-active-topic"

    _create_git_donkey_worktree(local_path, completed_branch)
    _create_git_donkey_worktree(local_path, topic_branch)
    _commit_completion_marker(local_path, "(#791)")
    scenario = PlonkScenario(
        local_path=local_path,
        completed_branch=completed_branch,
        active_branch=topic_branch,
    )

    monkeypatch.chdir(scenario.worktree_path(topic_branch))
    exit_code = plonk.run_git_plonk()

    assert exit_code == 0, "expected default git plonk to succeed from topic worktree"
    assert not scenario.worktree_path(completed_branch).exists(), (
        "expected main-history completion marker to remove completed worktree"
    )
    assert scenario.worktree_path(topic_branch).exists(), (
        "expected active topic worktree to remain"
    )
    assert completed_branch in Repo(local_path).heads, (
        "expected default mode to keep completed branch"
    )


@pytest.mark.parametrize("hard", [False, True])
def test_git_plonk_keeps_invoking_completed_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    hard: bool,
) -> None:
    """Default and hard modes should not remove their invoking worktree."""
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    completed_branch = "issue-793-invoking-completed-worktree"

    _create_git_donkey_worktree(local_path, completed_branch)
    _commit_completion_marker(local_path, "(#793)")
    scenario = PlonkScenario(
        local_path=local_path,
        completed_branch=completed_branch,
    )

    monkeypatch.chdir(scenario.worktree_path(completed_branch))
    exit_code = plonk.run_git_plonk(hard=hard)

    assert exit_code == 0, "expected git plonk to succeed from completed worktree"
    assert scenario.worktree_path(completed_branch).exists(), (
        "expected invoking completed worktree to remain"
    )
    assert completed_branch in Repo(local_path).heads, (
        "expected invoking completed branch to remain"
    )


scenarios("features/git_plonk.feature")
