"""The checkout the ``git wheresat`` integration suites read, and the runs in it.

Both suites start from one repository shape: a child branch whose stack record
names a parent that is itself one commit ahead of the trunk, so the boundary the
record attests is a commit the trunk never held and no merge base recovers on
its own. ``test_wheresat_read_only.py`` runs the command's whole command line
against that checkout and measures what the repository looks like afterwards;
``test_wheresat_end_to_end.py`` plonks the parent first, so the same boundary has
to come back from the tombstone the sweep left behind.

``test_wheresat_record.py`` asks the command to refresh that record instead, and
so builds a checkout of its own per test rather than sharing one: a record write
is a change no later test could read the same repository past. The readers here
and in the record suite's binder take the artefacts back out of Git — the anchor
ref with ``rev-parse``, the record's values with ``config --list`` — so what the
suites assert on is the repository, not the run's account of it.

The reading the read-only suites compare lives apart, in
:mod:`tests.integration.wheresat_fingerprint`: what a whole repository's state
is measured as, and the one ref namespace a run may add to, are that module's
business rather than this one's. Here are the checkout, the runs made against
it, and the artefacts read back out of it.
"""

from __future__ import annotations

import contextlib
import dataclasses
import enum
import json
import typing as typ

from git import Repo

from git_donkey import stack_records, wheresat, wheresat_github
from git_donkey.wheresat_errors import WheresatGitHubError
from tests import git_repo_helpers
from tests.integration.conftest import _setup_repo
from tests.integration.plonk_helpers import (
    branch_ahead_of_trunk,
    create_stacked_git_donkey_worktree,
)
from tests.integration.wheresat_fingerprint import Fingerprint, fingerprint

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    import pytest

    from git_donkey.wheresat_graph import WheresatGraph
    from git_donkey.wheresat_records import ParentPullRequest

CHILD: typ.Final = "child"
"""Branch the suites ask ``git wheresat`` about."""

PARENT: typ.Final = "parent"
"""Branch the child was cut from, one commit ahead of the trunk."""


class Status(enum.IntEnum):
    """The statuses the command documents, as the numbers it exits with.

    The suites assert on these rather than on bare integers because an exit
    code is the one reading a run can get wrong without any text to show for
    it, and because a number written beside an assertion says which status was
    expected only to a reader who has the table open. The values are the
    process exit codes, and the enum is an ``int`` so ``==`` still compares
    them with a run's.

    """

    ESTABLISHED = 0
    """The run answered the question it was asked."""

    REFUSED = 1
    """The run's question was answered, and the answer declined."""

    UNUSABLE = 2
    """The run could not start, or its argument named something unusable."""

    INDETERMINATE = 3
    """A question the run needed went unanswered, so it cannot say."""


def worktree_root(checkout: Path) -> Path:
    """Return the root ``git donkey`` puts a checkout's worktrees under.

    One place names the directory, so a suite that reads a worktree directly
    and a scenario that reports one cannot disagree about where it is.

    Parameters
    ----------
    checkout : Path
        Working checkout the worktrees were created for.

    Returns
    -------
    Path
        The directory, named after the checkout, that holds one directory per
        branch.

    """
    return checkout.parent / f"{checkout.name}.worktrees"


@dataclasses.dataclass(frozen=True, slots=True)
class WheresatScenario:
    """A checkout holding a stacked child, and the boundary its record attests.

    Attributes
    ----------
    local_path : Path
        Working checkout the parent was grown in and the child was cut from.
    remote_path : Path
        Bare repository the checkout pushes to and fetches from.
    boundary : str
        Commit the child's stack record attests: the parent's own tip at the
        moment the child was cut from it.
    tip : str
        Commit the child branch was left at, one commit above the boundary.
    child : str
        Branch the suites ask about, which ``git donkey`` also made a worktree
        for.
    parent : str
        Branch the child was cut from.

    """

    local_path: Path
    remote_path: Path
    boundary: str
    tip: str
    child: str = CHILD
    parent: str = PARENT

    @contextlib.contextmanager
    def repo(self) -> cabc.Iterator[Repo]:
        """Yield the scenario's primary working checkout, closed at block end.

        The repository is opened for the reads made inside the block and closed
        when it ends, rather than handed out for the caller to close: a suite
        reads the same repository on both sides of a run, and a repository left
        open holds its object database open behind the reading.

        Yields
        ------
        git.Repo
            The checkout ``git donkey`` was run in, holding the branches, the
            record's configuration, and the anchor ref the suites assert on.

        """
        with Repo(self.local_path) as repo:
            yield repo

    @property
    def worktree_root(self) -> Path:
        """The git-donkey worktree root for the scenario repository."""
        return worktree_root(self.local_path)

    def worktree_path(self, branch_name: str | None = None) -> Path:
        """Return the worktree ``git donkey`` created for ``branch_name``.

        Parameters
        ----------
        branch_name : str | None, optional
            Branch whose worktree is named, defaulting to the scenario's child,
            which is the branch every suite here asks about.

        Returns
        -------
        pathlib.Path
            The directory that branch's worktree would occupy. It is computed
            rather than read, so a suite can name a worktree that was never
            created — which is how it shows that nothing created one.

        """
        return self.worktree_root / (branch_name or self.child)

    @contextlib.contextmanager
    def worktree_repo(self, branch_name: str | None = None) -> cabc.Iterator[Repo]:
        """Yield the worktree ``git donkey`` created for ``branch_name``.

        The worktree is opened in its own right, because a linked worktree is a
        second working tree of the same repository: its status and its Git
        directory answer about that checkout rather than about the one the
        command was run in.

        Parameters
        ----------
        branch_name : str | None, optional
            Branch whose worktree is opened, defaulting to the scenario's child.

        Yields
        ------
        git.Repo
            The worktree's own handle on the repository, closed at block end.

        """
        with Repo(self.worktree_path(branch_name)) as repo:
            yield repo

    def worktree_head(self, branch_name: str | None = None) -> str:
        """Return the commit the worktree for ``branch_name`` is checked out at.

        Parameters
        ----------
        branch_name : str | None, optional
            Branch whose worktree is read, defaulting to the scenario's child.

        Returns
        -------
        str
            The commit that worktree's ``HEAD`` names.

        """
        with self.worktree_repo(branch_name) as repo:
            return repo.head.commit.hexsha


def stacked_child(root: Path) -> WheresatScenario:
    """Build a checkout whose child branch has a record naming its parent.

    The parent is grown one commit ahead of the trunk and the child is cut from
    it by ``git donkey``, so the record attests a boundary the trunk does not
    name. The child's worktree is then committed to, which is what gives that
    boundary a replay range to partition: without work of its own the child
    would have nothing to replay, and the command would refuse the boundary for
    a reason that had nothing to do with the record.

    Parameters
    ----------
    root : Path
        Directory the checkout and its bare remote are created under.

    Returns
    -------
    WheresatScenario
        The scenario, with the boundary and the child tip recorded.

    """
    local_path, remote_path = _setup_repo(root)
    with in_directory(local_path):
        boundary = branch_ahead_of_trunk(local_path, PARENT)
        create_stacked_git_donkey_worktree(local_path, CHILD, PARENT)
    scenario = WheresatScenario(
        local_path=local_path,
        remote_path=remote_path,
        boundary=boundary,
        tip=boundary,
    )
    with scenario.worktree_repo() as worktree:
        git_repo_helpers.advance(worktree, message=f"work on {CHILD}")
    return dataclasses.replace(scenario, tip=scenario.worktree_head())


def reading(scenario: WheresatScenario) -> Fingerprint:
    """Return the fingerprint of both of the scenario's working trees.

    Parameters
    ----------
    scenario : WheresatScenario
        Scenario whose two working trees are read: the checkout the command was
        run in and the worktree ``git donkey`` made for the child.

    Returns
    -------
    Fingerprint
        The reading of the scenario's local and worktree checkouts, taken
        together as one reading by :func:`fingerprint`.

    """
    with scenario.repo() as repo:
        return fingerprint(
            scenario.local_path,
            scenario.worktree_path(),
            repo=repo,
        )


def anchor(scenario: WheresatScenario, branch: str = CHILD) -> str | None:
    """Return the commit ``branch``'s anchor ref names, if it has one.

    The child is the default because that is the branch every suite here reads;
    a branch the scenario has no record for is asked about explicitly, which is
    how a suite shows that a run wrote nothing for it.

    Parameters
    ----------
    scenario : WheresatScenario
        Scenario whose repository the ref is read from.
    branch : str, optional
        Branch whose anchor ref is read, defaulting to the child.

    Returns
    -------
    str | None
        The commit the ref names, or ``None`` when the branch has no anchor ref.

    """
    with scenario.repo() as repo:
        return git_repo_helpers.ref_value(repo, stack_records.base_ref_path(branch))


def configuration(scenario: WheresatScenario) -> dict[str, str]:
    """Return the child's branch configuration, read from Git directly.

    Parameters
    ----------
    scenario : WheresatScenario
        Scenario whose repository the configuration is read from.

    Returns
    -------
    dict[str, str]
        Every ``branch.<child>.*`` setting, keyed by the part after the
        ``branch.<child>.`` prefix, as Git's own ``--list`` reports them.

    """
    with scenario.repo() as repo:
        return git_repo_helpers.config_section(repo, CHILD)


def forget_anchor(scenario: WheresatScenario, branch: str = CHILD) -> None:
    """Delete ``branch``'s anchor ref, leaving its configuration behind.

    A record whose anchor was collected is the state a refresh exists for: the
    configuration still names the boundary, and the write that makes it
    reachable again is the create-only half of INV-7.

    Parameters
    ----------
    scenario : WheresatScenario
        The checkout whose anchor ref is deleted.
    branch : str, optional
        Branch whose anchor ref is deleted.

    """
    with scenario.repo() as repo:
        repo.git.update_ref("-d", stack_records.base_ref_path(branch))


def forget_record(scenario: WheresatScenario, branch: str = CHILD) -> None:
    """Take ``branch``'s stack record out of the repository, ref and all.

    The anchor ref and the branch configuration are the two halves of a record
    a run reads, so both go: a run that found either would answer from a record
    the journey does not have. The record's own keys are unset one at a time
    rather than the whole section being removed, because the section is shared
    with everything else Git keeps about the branch — its remote, its merge
    base, and its upstream — and the journey has no business taking those.

    Parameters
    ----------
    scenario : WheresatScenario
        The checkout whose record is removed.
    branch : str, optional
        Branch whose record is removed.

    """
    with scenario.repo() as repo:
        for key in stack_records.RecordKey:
            repo.git.config(
                "--local",
                "--unset-all",
                f"branch.{branch}.{key.value}",
                with_exceptions=False,
            )
    forget_anchor(scenario, branch)


@contextlib.contextmanager
def in_directory(path: Path) -> cabc.Iterator[None]:
    """Run the block with the process current directory set to ``path``.

    The command reads its repository from the current directory, exactly as the
    console script does, so entering a checkout is what lets a suite exercise
    the whole command line rather than calling the workflow with a repository
    handed to it.

    Parameters
    ----------
    path : Path
        Directory to enter for the duration of the block.

    Yields
    ------
    None
        After the current directory has been set, and restores the previous one
        however the block ends.

    """
    with contextlib.chdir(path):
        yield


@dataclasses.dataclass(frozen=True, slots=True)
class WheresatRun:
    """What one ``git wheresat`` run reported.

    Attributes
    ----------
    exit_code : int
        Status the workflow returned.
    stdout : str
        What the run wrote to standard output.
    stderr : str
        What the run wrote to standard error.

    """

    exit_code: int
    stdout: str
    stderr: str

    @property
    def envelope(self) -> dict[str, object]:
        """Standard output parsed as the command's JSON envelope."""
        return json.loads(self.stdout)


_UNANSWERED: typ.Final = "this suite's forge answers nothing about a pull request"
"""Why the suite's double refuses every question about a pull request."""


def examined_and_found_none(
    commits: cabc.Sequence[str],
) -> wheresat_github.AssociationPage:
    """Return the page of a search that examined every commit and found none.

    Every double in these suites answers the association search this way, and
    the answer is the delicate one: a search that failed would make every local
    refusal indeterminate, because a fault means the evidence set is not known
    to be complete. Written once, the two doubles cannot drift into answering
    differently about the one question the suites' local refusals depend on.

    Parameters
    ----------
    commits : collections.abc.Sequence[str]
        The commits the search was asked about, none of which is associated
        with any pull request.

    Returns
    -------
    wheresat_github.AssociationPage
        A complete, untruncated page with an empty association for each commit.

    """
    return wheresat_github.AssociationPage(
        associations=dict.fromkeys(commits, ()),
        commits_examined=len(commits),
        truncated=False,
    )


class _AssociationsOnlyForge(wheresat_github.WheresatGitHub):
    """The forge the local suites hand the run in place of GitHub.

    The command opens the real port for a run that was handed none and is not
    ``--offline``, so a suite that ran the whole command line would attempt live
    traffic. This is what it is handed instead, and its two halves are the two
    states the local suites are entitled to. The association search answers —
    nothing is associated with the child's commits — so a refusal the run
    reaches locally stays a local refusal rather than becoming a question the
    forge could not answer. Every question about a pull request goes unanswered,
    so a run that named a parent reports the gates about it as unanswered. A
    double that refused everything would make every local refusal indeterminate,
    because a fault means the evidence set is not known to be complete.

    """

    @typ.override
    def pull_request(
        self, identity: stack_records.PullRequestIdentity
    ) -> ParentPullRequest:
        """Refuse: this forge knows nothing about any pull request."""
        raise WheresatGitHubError(_UNANSWERED)

    @typ.override
    def pull_request_body(self, identity: stack_records.PullRequestIdentity) -> str:
        """Refuse, for the same reason."""
        raise WheresatGitHubError(_UNANSWERED)

    @typ.override
    def stack_parent(
        self, identity: stack_records.PullRequestIdentity
    ) -> stack_records.PullRequestIdentity | None:
        """Refuse, for the same reason."""
        raise WheresatGitHubError(_UNANSWERED)

    @typ.override
    def associated_pull_requests(
        self, repository: str, commits: cabc.Sequence[str]
    ) -> wheresat_github.AssociationPage:
        """Return the page of a search that examined every commit and found none."""
        return examined_and_found_none(commits)


_THE_FORGE: typ.Final = _AssociationsOnlyForge()
"""The singleton double every run from this module is handed."""


def run_wheresat(
    options: wheresat.WheresatOptions,
    capsys: pytest.CaptureFixture[str],
    *,
    graph: WheresatGraph | None = None,
    forge: wheresat_github.WheresatGitHub | None = None,
) -> WheresatRun:
    """Run the command and record everything it reported.

    The repository is the current directory's, so a caller enters a checkout
    before calling this; nothing here reaches for the scenario the suite built,
    which is what keeps the measurement honest about what the command line can
    see for itself. The forge is the one exception, and it is not a shortcut:
    the command consults GitHub, so the choice is between a double and live
    traffic, and :class:`_AssociationsOnlyForge` is the double whose answers
    leave the local suites' refusals local.

    Parameters
    ----------
    options : wheresat.WheresatOptions
        What the command line asked for.
    capsys : pytest.CaptureFixture[str]
        Capture fixture the run's output is read from.
    graph : wheresat_graph.WheresatGraph | None, optional
        Read-only history questions. A Git-backed graph over the current
        directory's repository is used when it is omitted, which is what every
        suite but one wants; a suite that has to see *which* questions a run
        put hands a graph that records them.
    forge : wheresat_github.WheresatGitHub | None, optional
        The GitHub port to hand the run, for a suite that has to see what a run
        does with an answer GitHub gave. Omitted, the run is handed
        :data:`_THE_FORGE`. It is never handed ``None`` in this position: the
        command reads a forge of ``None`` as "open the real one", so passing
        the argument through would put live traffic in the suite's way.

    Returns
    -------
    WheresatRun
        The exit status and both output streams.

    """
    opener = _THE_FORGE if forge is None else forge
    exit_code = wheresat.run_git_wheresat(options, github=opener, graph=graph)
    captured = capsys.readouterr()
    return WheresatRun(
        exit_code=exit_code,
        stdout=captured.out,
        stderr=captured.err,
    )


type Where = typ.Literal["worktree", "checkout"]
"""Which of a scenario's two working trees a run is made from."""


def working_tree(scenario: WheresatScenario, where: Where) -> Path:
    """Return the working tree of ``scenario`` that ``where`` names.

    The command reads its repository from the current directory, so a suite
    enters one of these before running it: the child's worktree, where the
    branch is checked out, or the checkout itself, which is where a run about
    any other branch has to be made from.

    Parameters
    ----------
    scenario : WheresatScenario
        The checkout whose working trees are asked about.
    where : Where
        Working tree to enter.

    Returns
    -------
    Path
        The directory a run is made from.

    """
    return scenario.worktree_path() if where == "worktree" else scenario.local_path


def run_wheresat_at(
    directory: Path,
    options: wheresat.WheresatOptions,
    capsys: pytest.CaptureFixture[str],
    *,
    forge: wheresat_github.WheresatGitHub | None = None,
) -> WheresatRun:
    """Run the command from ``directory``, which is read as the repository.

    Parameters
    ----------
    directory : Path
        Working tree the run is made from, which the command reads as the
        repository it is about.
    options : wheresat.WheresatOptions
        What the command line asked for.
    capsys : pytest.CaptureFixture[str]
        Capture fixture the run's output is read from.
    forge : wheresat_github.WheresatGitHub | None, optional
        The GitHub port to hand the run, passed through to
        :func:`run_wheresat`.

    Returns
    -------
    WheresatRun
        The status and both output streams, which are the run's own: a suite
        that built its checkout inside the test leaves the ``git donkey`` that
        built it in the capture, so the capture is drained first.

    """
    capsys.readouterr()
    with in_directory(directory):
        return run_wheresat(options, capsys, forge=forge)


def run_wheresat_in(
    scenario: WheresatScenario,
    options: wheresat.WheresatOptions,
    capsys: pytest.CaptureFixture[str],
    *,
    where: Where = "worktree",
) -> WheresatRun:
    """Run the command from one of the scenario's working trees.

    The child's worktree is where the branch is checked out, so no ``--branch``
    is needed there; the checkout is where a run about any other branch has to
    be made from, because the command reads its repository from the current
    directory.

    Parameters
    ----------
    scenario : WheresatScenario
        The checkout the run is made against.
    options : wheresat.WheresatOptions
        What the command line asked for.
    capsys : pytest.CaptureFixture[str]
        Capture fixture the run's output is read from.
    where : Where, optional
        Working tree to run from, which :func:`working_tree` resolves.

    Returns
    -------
    WheresatRun
        The exit status and both output streams.

    """
    return run_wheresat_at(working_tree(scenario, where), options, capsys)


def report_tokens(output: str) -> set[tuple[str, ...]]:
    """Return a report's lines as token tuples, so column alignment is ignored.

    Both integration suites read the report's tables this way: a row is a fact
    about which fields it holds rather than about how wide the columns were
    padded, so a change to the report's widths does not move a single assertion.

    Parameters
    ----------
    output : str
        What a run wrote to standard output.

    Returns
    -------
    set[tuple[str, ...]]
        One tuple of whitespace-separated tokens per line.

    """
    return {tuple(line.split()) for line in output.splitlines()}
