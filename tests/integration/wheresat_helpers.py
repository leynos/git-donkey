"""The checkout the ``git wheresat`` integration suites read, and its fingerprint.

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

The fingerprint is the suites' shared measurement, and it is deliberately wider
than any one test's interest: INV-1 is a claim about a whole repository — every
ref and the commit it names, the index and the working tree, the stash, the
local configuration, and ``FETCH_HEAD`` — so the suites compare a reading of all
of it rather than the handful of refs a test happened to think of. Its
sensitivity is checked separately, by mutating a repository one reading at a
time and requiring the comparison to report it.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import typing as typ
from pathlib import Path

from git import GitCommandError, Repo

from git_donkey import stack_records, wheresat, wheresat_github
from git_donkey.wheresat_errors import WheresatGitHubError
from tests import git_repo_helpers
from tests.integration.conftest import _setup_repo
from tests.integration.plonk_helpers import (
    branch_ahead_of_trunk,
    create_stacked_git_donkey_worktree,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    import pytest

    from git_donkey.wheresat_graph import WheresatGraph
    from git_donkey.wheresat_records import ParentPullRequest

CHILD: typ.Final = "child"
"""Branch the suites ask ``git wheresat`` about."""

PARENT: typ.Final = "parent"
"""Branch the child was cut from, one commit ahead of the trunk."""

EVIDENCE_NAMESPACE: typ.Final = "refs/wheresat/"
"""The one namespace a run without ``--record`` may add refs to (INV-1)."""

_GIT_ENTRY: typ.Final = ".git"
"""Git's own directory, which a fingerprint skips."""

_FETCH_HEAD: typ.Final = "FETCH_HEAD"
"""The file a fetch writes, which INV-1 promises is unchanged."""


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

    @property
    def repo(self) -> Repo:
        """The primary working checkout of the scenario repository."""
        return Repo(self.local_path)

    @property
    def worktree_root(self) -> Path:
        """The git-donkey worktree root for the scenario repository."""
        return self.local_path.parent / f"{self.local_path.name}.worktrees"

    def worktree_path(self, branch_name: str | None = None) -> Path:
        """Return the worktree ``git donkey`` created for ``branch_name``."""
        return self.worktree_root / (branch_name or self.child)

    def worktree_repo(self, branch_name: str | None = None) -> Repo:
        """Open the worktree ``git donkey`` created for ``branch_name``."""
        return Repo(self.worktree_path(branch_name))

    def worktree_head(self, branch_name: str | None = None) -> str:
        """Return the commit the worktree for ``branch_name`` is checked out at."""
        return self.worktree_repo(branch_name).head.commit.hexsha


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
    git_repo_helpers.advance(scenario.worktree_repo(), message=f"work on {CHILD}")
    return dataclasses.replace(scenario, tip=scenario.worktree_head())


def _ref_value(repo: Repo, ref: str) -> str | None:
    """Return the commit ``ref`` names, or ``None`` when it does not exist."""
    try:
        return str(repo.git.rev_parse("--verify", "--quiet", ref))
    except GitCommandError:
        return None


def reading(scenario: WheresatScenario) -> Fingerprint:
    """Return the fingerprint of both of the scenario's working trees.

    Returns
    -------
    Fingerprint
        The reading of the scenario's local and worktree checkouts, taken
        together as one reading by :func:`fingerprint`.

    """
    return fingerprint(
        scenario.local_path,
        scenario.worktree_path(),
        repo=scenario.repo,
    )


def anchor(scenario: WheresatScenario, branch: str = CHILD) -> str | None:
    """Return the commit ``branch``'s anchor ref names, if it has one.

    The child is the default because that is the branch every suite here reads;
    a branch the scenario has no record for is asked about explicitly, which is
    how a suite shows that a run wrote nothing for it.

    Returns
    -------
    str | None
        The commit the ref names, or ``None`` when the branch has no anchor ref.

    """
    return _ref_value(scenario.repo, stack_records.base_ref_path(branch))


def configuration(scenario: WheresatScenario) -> dict[str, str]:
    """Return the child's branch configuration, read from Git directly.

    Returns
    -------
    dict[str, str]
        Every ``branch.<child>.*`` setting, keyed by the part after the
        ``branch.<child>.`` prefix, as Git's own ``--list`` reports them.

    """
    prefix = f"branch.{CHILD}."
    return {
        key[len(prefix) :]: value
        for entry in scenario.repo.git.config("--local", "--list", "-z").split("\0")
        if entry
        for key, _, value in (entry.partition("\n"),)
        if key.startswith(prefix)
    }


def forget_anchor(scenario: WheresatScenario) -> None:
    """Delete the child's anchor ref, leaving its configuration behind.

    A record whose anchor was collected is the state a refresh exists for: the
    configuration still names the boundary, and the write that makes it
    reachable again is the create-only half of INV-7.
    """
    scenario.repo.git.update_ref("-d", stack_records.base_ref_path(CHILD))


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
        return wheresat_github.AssociationPage(
            associations=dict.fromkeys(commits, ()),
            commits_examined=len(commits),
            truncated=False,
        )


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


@dataclasses.dataclass(frozen=True, slots=True)
class Fingerprint:
    """Everything about a repository and its working trees that a run may not change.

    Attributes
    ----------
    refs : tuple[str, ...]
        Every ref and the commit it names, as ``git for-each-ref`` lists them.
    status : tuple[str, ...]
        ``git status --porcelain=v2 --branch`` for each working tree read, with
        the tree's own directory name prefixed so two trees cannot be confused
        for one.
    stashes : tuple[str, ...]
        ``git stash list``.
    config : tuple[str, ...]
        ``git config --local --list``.
    fetch_head : tuple[str, ...]
        A digest of each working tree's ``FETCH_HEAD``, or that it has none,
        with the tree's own directory name prefixed like ``status``, because
        each tree has a file of its own to leave alone.
    files : tuple[str, ...]
        Every file each working tree holds, with its digest, named by the tree
        it belongs to.

    """

    refs: tuple[str, ...]
    status: tuple[str, ...]
    stashes: tuple[str, ...]
    config: tuple[str, ...]
    fetch_head: tuple[str, ...]
    files: tuple[str, ...]

    def differences(
        self,
        other: Fingerprint,
        *,
        allowed: str = EVIDENCE_NAMESPACE,
    ) -> tuple[str, ...]:
        """Return how ``other`` differs from this fingerprint.

        Parameters
        ----------
        other : Fingerprint
            Reading taken later — after a run, or after whatever else the
            caller is measuring.
        allowed : str, optional
            Ref namespace a change to which is not a difference. A run without
            ``--record`` may write evidence refs of its own, and nothing else:
            this is the whole of INV-1's permitted difference.

        Returns
        -------
        tuple[str, ...]
            One description per reading that differs, naming what was removed
            and what was added, so a failure says which part of the repository
            a run touched rather than only that something did.

        """
        differences = []
        for label, mine, theirs in (
            ("refs", _outside(self.refs, allowed), _outside(other.refs, allowed)),
            ("status", self.status, other.status),
            ("stashes", self.stashes, other.stashes),
            ("config", self.config, other.config),
            ("fetch-head", self.fetch_head, other.fetch_head),
            ("files", self.files, other.files),
        ):
            if mine == theirs:
                continue
            differences.append(
                f"{label}: removed {sorted(set(mine) - set(theirs))}, "
                f"added {sorted(set(theirs) - set(mine))}"
            )
        return tuple(differences)


def fingerprint(*roots: Path, repo: Repo) -> Fingerprint:
    """Return everything about ``repo`` and ``roots`` a read-only run must leave alone.

    Parameters
    ----------
    roots : Path
        Working trees to measure. Each one's index, working tree, and files are
        read separately, because a linked worktree is a second working tree of
        the same repository and a run could disturb either.
    repo : git.Repo
        Repository the refs, stash, and local configuration are read from. It is
        named rather than derived from a root, because several working trees
        share one repository.

    Returns
    -------
    Fingerprint
        The readings, comparable with :meth:`Fingerprint.differences`.

    """
    statuses: list[str] = []
    files: list[str] = []
    fetch_heads: list[str] = []
    for root in roots:
        working = Repo(root)
        statuses += [
            f"{root.name}: {line}"
            for line in _lines(
                working.git.status("--porcelain=v2", "--branch"),
            )
        ]
        fetch_heads += [f"{root.name}: {_fetch_head(working)}"]
        files += [f"{root.name}/{name} {digest}" for name, digest in _files(root)]
    return Fingerprint(
        refs=_lines(repo.git.for_each_ref("--format=%(refname) %(objectname)")),
        status=tuple(statuses),
        stashes=_lines(repo.git.stash("list")),
        config=_lines(repo.git.config("--local", "--list")),
        fetch_head=tuple(fetch_heads),
        files=tuple(files),
    )


def _outside(refs: cabc.Iterable[str], namespace: str) -> tuple[str, ...]:
    """Return the refs that are not inside ``namespace``."""
    return tuple(ref for ref in refs if not ref.startswith(namespace))


def _lines(output: str) -> tuple[str, ...]:
    """Return Git's output as the non-empty lines it holds."""
    return tuple(line for line in str(output).splitlines() if line)


def _files(root: Path) -> cabc.Iterator[tuple[str, str]]:
    """Yield the path and digest of every file in the working tree at ``root``.

    Git's own directory is skipped. The index and its caches are rewritten by
    reads as well as by writes, so comparing them would report changes that hold
    nothing; everything else in the tree is digested, tracked or not, because a
    run that reads must not leave a file behind either.

    Yields
    ------
    tuple[str, str]
        The path relative to ``root``, and the digest of its contents.

    """
    for directory, subdirectories, names in root.walk():
        subdirectories[:] = sorted(
            name for name in subdirectories if name != _GIT_ENTRY
        )
        for name in sorted(names):
            if name == _GIT_ENTRY:
                continue
            path = directory / name
            yield path.relative_to(root).as_posix(), _digest(path)


def _digest(path: Path) -> str:
    """Return the digest of the file at ``path``."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fetch_head(repo: Repo) -> str:
    """Return a digest of ``repo``'s ``FETCH_HEAD``, or that it has none.

    A fetch writes this file even when its refspec names a destination, so it is
    part of what INV-1 promises is unchanged. It is read from the Git directory
    the repository resolves to, which for a linked worktree is that worktree's
    own directory rather than the main checkout's: a run that fetched in one
    working tree would otherwise leave the other's file untouched and pass.
    :func:`fingerprint` therefore reads it once per working tree it measures,
    beside that tree's status and files, rather than once for the repository.

    Returns
    -------
    str
        The digest, or ``absent`` when the repository has no ``FETCH_HEAD``.

    """
    path = Path(repo.git.rev_parse("--absolute-git-dir")) / _FETCH_HEAD
    return _digest(path) if path.is_file() else "absent"
