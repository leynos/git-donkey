"""Behaviour-driven tests for ``git donkey`` in a bare repository collection.

The scenarios in ``features/git_donkey_bare.feature`` run against a real bare
repository whose only checkouts are linked worktrees, the layout a session
manager such as Lody keeps: ``<container>/bare.git`` beside
``<container>/worktrees/<id>``. They pin where new worktrees are placed when no
main worktree exists, that the session manager's own directory is left alone,
and that a remote unable to track branches is refused rather than silently
branched from.
"""

from __future__ import annotations

import dataclasses
import typing as typ
from pathlib import Path

from git import Repo
from pytest_bdd import given, scenarios, then, when

from git_donkey import layout
from tests.git_repo_helpers import bare_collection
from tests.integration.conftest import _setup_repo
from tests.integration.donkey_helpers import DonkeyScenario, run_donkey, seed_repo

if typ.TYPE_CHECKING:
    import pytest

_BRANCH = "feature/bare-layout"
_CONFIGURED_ROOT = "../donkeys"


@dataclasses.dataclass(slots=True)
class BareScenario(DonkeyScenario):
    """A donkey scenario whose repository is a bare collection.

    ``local_path`` is the directory the workflow is invoked from: the linked
    session worktree, or the bare directory itself.

    Attributes
    ----------
    container_path : Path
        Directory holding the bare repository and its worktree directories.
    session_path : Path
        The linked worktree the collection was created with.
    expected_root : Path
        Worktree root the scenario expects ``git donkey`` to use.

    """

    container_path: Path = dataclasses.field(default_factory=Path)
    session_path: Path = dataclasses.field(default_factory=Path)
    expected_root: Path = dataclasses.field(default_factory=Path)

    @property
    def bare_repo(self) -> Repo:
        """The bare repository at the centre of the collection."""
        return Repo(self.container_path / "bare.git")

    @property
    def new_worktree(self) -> Path:
        """Where the scenario expects the requested worktree to be created."""
        return self.expected_root / self.branch


def _bare_scenario(tmp_path: Path) -> BareScenario:
    """Build a bare collection cloned from a seeded remote."""
    _local_path, remote_path = _setup_repo(tmp_path)
    container_path = tmp_path / "container"
    _bare_repo, session_path = bare_collection(container_path, remote_path)
    remote_tip = Repo(remote_path).commit("main").hexsha
    return BareScenario(
        local_path=session_path,
        remote_path=remote_path,
        branch=_BRANCH,
        remote_tip=remote_tip,
        container_path=container_path,
        session_path=session_path,
        expected_root=container_path / "bare.worktrees",
    )


@given(
    "a bare repository collection entered through a linked worktree",
    target_fixture="scenario",
)
def bare_collection_from_linked_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> BareScenario:
    """Enter the collection through its session worktree."""
    scenario = _bare_scenario(tmp_path)
    monkeypatch.chdir(scenario.session_path)
    return scenario


@given(
    "a bare repository collection entered through the bare directory",
    target_fixture="scenario",
)
def bare_collection_from_bare_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> BareScenario:
    """Enter the collection through the bare repository itself."""
    scenario = _bare_scenario(tmp_path)
    scenario.local_path = scenario.container_path / "bare.git"
    monkeypatch.chdir(scenario.local_path)
    return scenario


@given(
    "a bare repository collection whose session branch is ahead of the remote default",
    target_fixture="scenario",
)
def bare_collection_with_session_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> BareScenario:
    """Commit work on the session branch that the remote default lacks."""
    scenario = _bare_scenario(tmp_path)
    monkeypatch.chdir(scenario.session_path)
    session_repo = Repo(scenario.session_path)
    seed_repo(session_repo, "session.txt", "session work")
    scenario.local_tip = session_repo.head.commit.hexsha
    return scenario


@given(
    "a bare repository collection with a configured worktree root",
    target_fixture="scenario",
)
def bare_collection_with_configured_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> BareScenario:
    """Point ``donkey.worktreesRoot`` at a directory beside the bare repository."""
    scenario = _bare_scenario(tmp_path)
    monkeypatch.chdir(scenario.session_path)
    scenario.bare_repo.git.config(layout.WORKTREES_ROOT_CONFIG_KEY, _CONFIGURED_ROOT)
    scenario.expected_root = scenario.container_path / "donkeys"
    return scenario


@given(
    "a bare repository collection whose remote has no fetch refspec",
    target_fixture="scenario",
)
def bare_collection_without_fetch_refspec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> BareScenario:
    """Leave the remote as ``git clone --bare`` configures it."""
    scenario = _bare_scenario(tmp_path)
    monkeypatch.chdir(scenario.session_path)
    scenario.bare_repo.git.config("--unset-all", "remote.origin.fetch")
    return scenario


@when("I run git donkey with the current branch as the base")
def run_donkey_with_current_branch_base(
    scenario: BareScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run the workflow with the invoking worktree's branch as the base."""
    run_donkey(scenario, capsys, ".")


@then("the new worktree is created under the bare repository's worktree root")
@then("the new worktree is created under the configured worktree root")
def new_worktree_is_under_expected_root(scenario: BareScenario) -> None:
    """Assert the worktree was registered at the expected path and branch."""
    assert scenario.new_worktree.is_dir(), (
        f"expected a worktree at {scenario.new_worktree}"
    )
    assert Repo(scenario.new_worktree).active_branch.name == scenario.branch, (
        "expected the new worktree to check out the requested branch"
    )


@then("the new worktree starts at the remote default tip")
def new_worktree_starts_at_remote_default(scenario: BareScenario) -> None:
    """Assert the implicit base was the remote's advertised default."""
    head = Repo(scenario.new_worktree).head.commit.hexsha
    assert head == scenario.remote_tip, (
        "expected the new worktree to start at the remote default tip"
    )


@then("the new worktree starts at the session branch tip")
def new_worktree_starts_at_session_tip(scenario: BareScenario) -> None:
    """Assert the dot base resolved to the invoking worktree's branch."""
    head = Repo(scenario.new_worktree).head.commit.hexsha
    assert head == scenario.local_tip, (
        "expected the new worktree to start at the session branch tip"
    )


@then("the session worktree directory holds only the session worktree")
def session_directory_is_untouched(scenario: BareScenario) -> None:
    """Assert nothing was created beside the session manager's checkout."""
    entries = sorted(path.name for path in scenario.session_path.parent.iterdir())
    assert entries == [scenario.session_path.name], (
        "expected git donkey to leave the session worktree directory alone"
    )


@then("no worktree root is created")
def no_worktree_root_is_created(scenario: BareScenario) -> None:
    """Assert the refusal happened before any directory was created."""
    assert not scenario.expected_root.exists(), (
        "expected a refused run to create no worktree root"
    )
    assert scenario.branch not in scenario.bare_repo.heads, (
        "expected a refused run to create no branch"
    )


scenarios("features/git_donkey_bare.feature")
