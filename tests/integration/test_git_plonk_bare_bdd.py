"""Behaviour-driven tests for ``git plonk`` in a bare repository collection.

The scenarios in ``features/git_plonk_bare.feature`` run against a bare
repository whose checkouts are all linked worktrees. Besides the worktree
``git donkey`` creates, the collection holds a session worktree in the
directory a session manager such as Lody owns, checked out on a branch that
*also* carries a completed marker. A sweep must retire the donkey worktree and
leave that session worktree, its branch, and its generated directories exactly
as it found them: they belong to another tool, and possibly to a live session.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git import Repo
from pytest_bdd import given, scenarios, then, when

from git_donkey import donkey, plonk
from tests.git_repo_helpers import bare_collection
from tests.integration.conftest import _setup_repo
from tests.integration.donkey_helpers import seed_repo
from tests.integration.plonk_helpers import capture_run
from tests.output_assertions import require_stream, stream

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    import pytest

_SESSION_BRANCH = "issue-201-session"
_DONKEY_BRANCH = "issue-202-donkey"
_GENERATED_NAME = "node_modules"


@dataclasses.dataclass(slots=True)
class BarePlonkScenario:
    """Paths of a bare collection holding a session and a donkey worktree.

    The sweep's result is recorded alongside the paths, so each scenario can
    pin the report the command produced from wherever it was invoked.
    """

    bare_path: Path
    session_path: Path
    donkey_path: Path
    mode: str = ""
    exit_code: int | str | None = None
    stdout: str = ""
    stderr: str = ""

    @property
    def bare_repo(self) -> Repo:
        """The bare repository at the centre of the collection."""
        return Repo(self.bare_path)


def _record_run(
    scenario: BarePlonkScenario,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    call: cabc.Callable[[], int | None],
) -> None:
    """Run a successful sweep and assert it reported through stdout alone."""
    scenario.mode = mode
    result = capture_run(capsys, call)
    scenario.exit_code = result.exit_code
    scenario.stdout = result.stdout
    scenario.stderr = result.stderr
    assert scenario.exit_code == 0, "expected the bare-collection sweep to succeed"
    require_stream(scenario.stderr, "", context="git plonk stderr")


def _require_report(scenario: BarePlonkScenario, *lines: str) -> None:
    """Assert the sweep printed exactly ``lines``, each terminated by a newline."""
    require_stream(scenario.stdout, stream(*lines), context="git plonk stdout")


def _complete_on_trunk(session_path: Path, markers: tuple[str, ...]) -> None:
    """Publish commits carrying ``markers`` to the remote's default branch.

    A bare collection has no checkout of trunk, so the commits are made in a
    detached scratch worktree and pushed, leaving every branch in the
    collection where it was.
    """
    repo = Repo(session_path)
    scratch_path = session_path.parent.parent / "scratch"
    repo.git.worktree("add", "--detach", scratch_path.as_posix(), "origin/main")
    scratch_repo = Repo(scratch_path)
    for marker in markers:
        seed_repo(scratch_repo, f"done-{len(marker)}-{marker[2:-1]}.txt", marker)
        scratch_repo.git.commit("--amend", "-m", f"Complete work {marker}")
    scratch_repo.git.push("origin", "HEAD:refs/heads/main")
    repo.git.worktree("remove", scratch_path.as_posix())


def _collection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> BarePlonkScenario:
    """Build a collection with one session worktree and one donkey worktree."""
    _local_path, remote_path = _setup_repo(tmp_path)
    container_path = tmp_path / "container"
    _bare_repo, session_path = bare_collection(
        container_path,
        remote_path,
        session_branch=_SESSION_BRANCH,
    )
    monkeypatch.chdir(session_path)
    exit_code = donkey.run_git_donkey(_DONKEY_BRANCH)
    assert exit_code == 0, "expected git donkey to create the donkey worktree"
    monkeypatch.chdir(session_path)
    return BarePlonkScenario(
        bare_path=container_path / "bare.git",
        session_path=session_path,
        donkey_path=container_path / "bare.worktrees" / _DONKEY_BRANCH,
    )


@given(
    "a bare repository collection with a completed git donkey worktree",
    target_fixture="scenario",
)
def collection_with_completed_worktrees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> BarePlonkScenario:
    """Complete both branches on trunk, so only path can tell them apart."""
    scenario = _collection(tmp_path, monkeypatch)
    _complete_on_trunk(scenario.session_path, ("(#201)", "(#202)"))
    return scenario


@given(
    "a bare repository collection with generated directories in every worktree",
    target_fixture="scenario",
)
def collection_with_generated_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> BarePlonkScenario:
    """Seed the same generated directory in the session and donkey worktrees."""
    scenario = _collection(tmp_path, monkeypatch)
    for worktree_path in (scenario.session_path, scenario.donkey_path):
        generated_path = worktree_path / _GENERATED_NAME
        generated_path.mkdir()
        (generated_path / "package.txt").write_text("installed dependency")
    return scenario


@when("I run git plonk in the bare collection")
def run_default_plonk(
    scenario: BarePlonkScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run the default cleanup mode from the session worktree."""
    _record_run(scenario, capsys, "default", plonk.run_git_plonk)
    _require_report(
        scenario,
        f"git-plonk: mode={scenario.mode}",
        "Removed worktrees:",
        f"- {scenario.donkey_path}",
    )


@when("I run git plonk in hard mode in the bare collection")
def run_hard_plonk(
    scenario: BarePlonkScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run the hard cleanup mode from the session worktree."""
    _record_run(
        scenario,
        capsys,
        "hard",
        lambda: plonk.run_git_plonk(hard=True),
    )
    _require_report(
        scenario,
        f"git-plonk: mode={scenario.mode}",
        "Removed worktrees:",
        f"- {scenario.donkey_path}",
        "Removed branches:",
        f"- {_DONKEY_BRANCH}",
    )


@when("I run git plonk in soft mode in the bare collection")
def run_soft_plonk(
    scenario: BarePlonkScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run the soft cleanup mode from the session worktree."""
    _record_run(
        scenario,
        capsys,
        "soft",
        lambda: plonk.run_git_plonk(soft=True),
    )
    _require_report(
        scenario,
        f"git-plonk: mode={scenario.mode}",
        "Removed generated paths:",
        f"- {scenario.donkey_path / _GENERATED_NAME}",
    )


@when("I run git plonk from the bare directory")
def run_plonk_from_bare_directory(
    scenario: BarePlonkScenario,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run the default cleanup mode from inside the bare repository."""
    monkeypatch.chdir(scenario.bare_path)
    _record_run(scenario, capsys, "default", plonk.run_git_plonk)
    _require_report(
        scenario,
        f"git-plonk: mode={scenario.mode}",
        "Removed worktrees:",
        f"- {scenario.donkey_path}",
    )


@then("the completed git donkey worktree is removed")
def donkey_worktree_is_removed(scenario: BarePlonkScenario) -> None:
    """Assert the completed worktree under the donkey root is gone."""
    assert not scenario.donkey_path.exists(), (
        "expected the completed git donkey worktree to be removed"
    )


@then("the completed git donkey branch is deleted")
def donkey_branch_is_deleted(scenario: BarePlonkScenario) -> None:
    """Assert hard mode deleted the donkey worktree's local branch."""
    assert _DONKEY_BRANCH not in scenario.bare_repo.heads, (
        "expected hard mode to delete the completed git donkey branch"
    )


@then("the completed session worktree remains")
def session_worktree_remains(scenario: BarePlonkScenario) -> None:
    """Assert a completed worktree outside the donkey root survives."""
    assert scenario.session_path.is_dir(), (
        "expected the session worktree to survive the sweep"
    )
    assert Repo(scenario.session_path).active_branch.name == _SESSION_BRANCH, (
        "expected the session worktree to keep its branch checked out"
    )


@then("the completed session branch remains")
def session_branch_remains(scenario: BarePlonkScenario) -> None:
    """Assert hard mode never deletes a branch held outside the donkey root."""
    assert _SESSION_BRANCH in scenario.bare_repo.heads, (
        "expected hard mode to leave the session branch in place"
    )


@then("the git donkey worktree's generated directory is removed")
def donkey_generated_directory_is_removed(scenario: BarePlonkScenario) -> None:
    """Assert soft mode cleaned the worktree under the donkey root."""
    assert not (scenario.donkey_path / _GENERATED_NAME).exists(), (
        "expected soft mode to clean the git donkey worktree"
    )


@then("the session worktree's generated directory remains")
def session_generated_directory_remains(scenario: BarePlonkScenario) -> None:
    """Assert soft mode left the session manager's worktree untouched."""
    assert (scenario.session_path / _GENERATED_NAME / "package.txt").is_file(), (
        "expected soft mode to leave the session worktree's generated directory"
    )


scenarios("features/git_plonk_bare.feature")
