"""Repository builders shared by the ``git donkey`` behavioural suites.

The scenarios bound in ``test_git_donkey_bases_bdd.py`` and
``test_git_donkey_reuse_bdd.py`` compose the same temporary repository: a
working clone with a bare remote, optionally a base branch left behind its
remote counterpart, and whichever branches, worktrees, or occupied paths the
scenario must collide with. Keeping that vocabulary in one place means a change
to how a scenario is seeded cannot leave the two suites asserting against
differently built repositories.
"""

from __future__ import annotations

import dataclasses
import typing as typ
from pathlib import Path

import pytest
from git import Repo

from git_donkey import donkey, stack_records, stack_store
from tests.integration.conftest import _setup_repo

if typ.TYPE_CHECKING:
    import collections.abc as cabc


@dataclasses.dataclass(slots=True)
class DonkeyScenario:
    """Repository state and workflow result shared by the git donkey steps.

    Attributes
    ----------
    local_path : Path
        Working checkout the workflow is invoked from.
    remote_path : Path
        Bare repository the working checkout pushes to and fetches from.
    branch : str
        Branch the scenario asks ``git donkey`` to create or reuse.
    remote_tip : str | None
        Commit at the tip of the relevant branch on the remote, when a
        scenario needs to assert against it.
    local_tip : str | None
        Commit at the tip of the relevant local branch before the run, when a
        scenario needs to assert it was preserved or reused.
    exit_code : int | str | None
        Exit code the workflow returned, or the code it exited with.
    stderr : str
        Standard error the workflow produced.

    """

    local_path: Path
    remote_path: Path
    branch: str
    remote_tip: str | None = None
    local_tip: str | None = None
    exit_code: int | str | None = None
    stderr: str = ""

    @property
    def repo(self) -> Repo:
        """The primary working checkout of the scenario repository."""
        return Repo(self.local_path)

    @property
    def worktree_root(self) -> Path:
        """The git-donkey worktree root for the scenario repository."""
        return self.local_path.parent / f"{self.local_path.name}.worktrees"

    def worktree_path(self, branch_name: str | None = None) -> Path:
        """Return the expected worktree path for ``branch_name``."""
        return self.worktree_root / (branch_name or self.branch)

    def worktree_repo(self, branch_name: str | None = None) -> Repo:
        """Open the worktree the workflow created for ``branch_name``."""
        return Repo(self.worktree_path(branch_name))

    def worktree_head(self, branch_name: str | None = None) -> str:
        """Return the commit the worktree for ``branch_name`` was started at."""
        return self.worktree_repo(branch_name).head.commit.hexsha


def new_scenario(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    branch: str,
) -> DonkeyScenario:
    """Create a clone with a bare remote and make the clone the working directory.

    Parameters
    ----------
    tmp_path : Path
        Directory the clone and its bare remote are created under.
    monkeypatch : pytest.MonkeyPatch
        Patcher used to enter the clone for the duration of the test.
    branch : str
        Branch the scenario will ask ``git donkey`` to create or reuse.

    Returns
    -------
    DonkeyScenario
        The seeded scenario, with no commits recorded yet.

    """
    local_path, remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    return DonkeyScenario(
        local_path=local_path,
        remote_path=remote_path,
        branch=branch,
    )


def seed_repo(repo: Repo, filename: str, content: str) -> None:
    """Commit one file into an existing repository.

    Parameters
    ----------
    repo : Repo
        Repository the file is written and committed into.
    filename : str
        Name of the file to create relative to the repository's working tree.
    content : str
        Content written to the file before it is committed.

    """
    path = Path(repo.working_tree_dir or ".") / filename
    path.write_text(content)
    repo.index.add([str(path)])
    repo.index.commit("Seed commit")


def leave_base_behind_remote(scenario: DonkeyScenario) -> None:
    """Push a commit to the remote and reset the local base back behind it.

    Records the pushed commit as ``remote_tip`` and the commit the local base
    is left at as ``local_tip``, so a scenario can assert which of the two the
    workflow chose.

    Parameters
    ----------
    scenario : DonkeyScenario
        Scenario whose ``main`` branch is left one commit behind its remote.

    """
    repo = scenario.repo
    seed_repo(repo, "upstream.txt", "upstream change")
    repo.remote("origin").push("main")
    scenario.remote_tip = repo.head.commit.hexsha
    repo.git.reset("--hard", "HEAD~1")
    scenario.local_tip = repo.head.commit.hexsha


def stack_record(scenario: DonkeyScenario, branch: str) -> stack_records.RecordResult:
    """Return what the stack record artefacts for ``branch`` reconcile to.

    The record is read through the command's own reader rather than from the
    configuration directly, so a scenario asserts against the artefact as the
    next command will see it — including the absence of one, which is a
    first-class result rather than an error.

    Parameters
    ----------
    scenario : DonkeyScenario
        Scenario whose repository holds the record.
    branch : str
        Branch the record was written for.

    Returns
    -------
    stack_records.RecordResult
        The reconciled record, or the reason there is none to use.

    """
    return stack_store.GitStackRecordReader(scenario.repo).read(branch)


def require_stack_record(
    scenario: DonkeyScenario, branch: str
) -> stack_records.StackRecord:
    """Return the stack record for ``branch``, failing the test if there is none.

    Parameters
    ----------
    scenario : DonkeyScenario
        Scenario whose repository holds the record.
    branch : str
        Branch the record was written for.

    Returns
    -------
    stack_records.StackRecord
        The reconciled record.

    """
    record = stack_record(scenario, branch)
    if not isinstance(record, stack_records.StackRecord):
        pytest.fail(f"expected a stack record for {branch!r}, got {record!r}")
    return record


def _record_run(
    scenario: DonkeyScenario,
    capsys: pytest.CaptureFixture[str],
    call: cabc.Callable[[], int],
) -> None:
    """Run ``call``, recording its exit code and stderr on ``scenario``."""
    try:
        scenario.exit_code = call()
    except SystemExit as exc:
        scenario.exit_code = exc.code
    finally:
        scenario.stderr = capsys.readouterr().err


def run_donkey(
    scenario: DonkeyScenario,
    capsys: pytest.CaptureFixture[str],
    base: str | None = None,
    *,
    options: donkey._PullOptions = donkey._DEFAULT_PULL_OPTIONS,
) -> None:
    """Run the git donkey workflow and record how it ended.

    A workflow that exits is recorded rather than raised, so a scenario states
    the exit code it expects in its ``Then`` steps instead of in its ``When``
    step.

    Parameters
    ----------
    scenario : DonkeyScenario
        Scenario supplying the branch name and collecting the result.
    capsys : pytest.CaptureFixture[str]
        Capture fixture the workflow's stderr is read from.
    base : str | None
        Explicit base branch, ``"."`` for the calling branch, or ``None`` for
        the principal remote's default branch.
    options : donkey._PullOptions
        Pull strategy to opt in to. Pulling is disabled by default.

    """
    _record_run(
        scenario,
        capsys,
        lambda: donkey.run_git_donkey(scenario.branch, base, options=options),
    )


def run_donkey_without_pulling(
    scenario: DonkeyScenario,
    capsys: pytest.CaptureFixture[str],
    base: str | None = None,
) -> None:
    """Run the git donkey workflow with the explicit ``--no-pull`` flag.

    Parameters
    ----------
    scenario : DonkeyScenario
        Scenario supplying the branch name and collecting the result.
    capsys : pytest.CaptureFixture[str]
        Capture fixture the workflow's stderr is read from.
    base : str | None
        Explicit base branch, ``"."`` for the calling branch, or ``None`` for
        the principal remote's default branch.

    """
    _record_run(
        scenario,
        capsys,
        lambda: donkey.run_git_donkey(scenario.branch, base, no_pull=True),
    )
