"""The GitHub-shaped journeys the ``git wheresat`` feature file is written against.

``test_git_wheresat_bdd.py`` binds ``features/git_wheresat.feature`` to the
builders here. Each builder returns the repository one journey happens in, along
with the commits the journey is about and the forge that stands in for GitHub:
the child's record, the trunk's squash commit, the parent's head, and the pull
request GitHub would answer with. The suite's assertions are then made against
Git — the fetch's destination ref, the fingerprint of the whole repository, the
commits a run names — rather than against the run's account of itself.

The squash shape is the feature's Background: a child cut from a parent branch
one commit ahead of the trunk, the parent squash-merged into the trunk, and the
pull request recorded against the parent's head. Every journey starts there and
moves one of its facts: the record is taken away, the parent is rewritten before
it merges, the pull request's head is held by a fork instead of by origin, or
the repository's history is cut at the child's tip. The rewritten shape is
``tests.git_repo_helpers.rewritten_parent_stack``'s, re-wrapped here with the
remote, the record-less checkout, and the scripted forge a run needs.

The forge is scripted rather than recorded because these journeys ask about
traffic the cassettes do not hold: a pull request whose head lives in a fork, and
a fetch of that head from the fork. A cassette is recorded only for a command
meant to call the API, so the doubles here answer with the metadata a real run
would have read, and the fetch it drives is a real fetch against a bare
repository the ``url.<path>.insteadOf`` rewrite puts in the fork's place.

This module is not a test module: it defines no ``test_`` functions and holds no
examples of its own. Its own checks therefore raise :class:`AssertionError`
directly rather than using ``assert``, which the lint configuration permits only
in modules pytest collects.

"""

from __future__ import annotations

import contextlib
import dataclasses
import typing as typ

from git import GitCommandError, Repo

from git_donkey import stack_records, wheresat_github
from git_donkey.wheresat_records import ParentPullRequest
from tests import git_repo_helpers
from tests.integration.wheresat_helpers import (
    CHILD,
    PARENT,
    Fingerprint,
    WheresatScenario,
    examined_and_found_none,
    fingerprint,
    reading,
    run_wheresat_at,
    stacked_child,
    working_tree,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    import pytest

    from git_donkey import wheresat
    from tests.integration.wheresat_helpers import Where, WheresatRun

REPOSITORY: typ.Final = "octocat/hello-world"
"""Repository the child lives in, which its principal remote names."""

FORK: typ.Final = "contributor/hello-world"
"""Repository the parent's head lives in when the pull request came from a fork."""

NUMBER: typ.Final = 42
"""Number of the parent pull request, as ``--parent`` spells it."""

PULL_REQUEST: typ.Final = f"{REPOSITORY}#{NUMBER}"
"""The parent pull request, as the command line names it."""

MERGED_AT: typ.Final = "2026-09-01T09:30:00Z"
"""When the forge says the parent pull request merged."""

_CHILD_WORK: typ.Final = "child-work.txt"
"""File the child's own work is committed to, above the inherited boundary."""

_RESTORING: typ.Final = "Restore the incoming content"
"""Message of the commit that puts the child's tree back at the landed tree."""

_LATER: typ.Final = "later.txt"
"""File a commit above the restoring one is made to."""


def _expect(condition: object, message: str) -> None:
    """Fail the fixture when what it believes it built is not what it built.

    Parameters
    ----------
    condition : object
        What the builder asserts of the repository it just made. Any object is
        accepted so the caller can pass a truthy value of any type, such as the
        commit a lookup returned.
    message : str
        What was expected, said as the failure rather than as the assertion.

    Raises
    ------
    AssertionError
        If ``condition`` is falsy. The module is not collected by pytest, so
        this is raised rather than asserted.

    """
    if not condition:
        raise AssertionError(message)


class ScriptedForge(wheresat_github.WheresatGitHub):
    """A forge that knows one pull request and associates nothing with anything.

    The parent's head has to be fetched for the run to weigh the gates about it,
    and the fetch is driven by the metadata a real run would have read: this is
    that metadata, and nothing else. The association search answers with a page
    that examined the commits it was asked about and found no pull request, which
    is the answer that leaves a run's local refusals local: a search that failed
    would make every one of them indeterminate instead.

    Parameters
    ----------
    pull : ParentPullRequest
        The parent pull request, which the fork journey replaces with the same
        pull request whose head lives in a fork.

    """

    def __init__(self, pull: ParentPullRequest) -> None:
        self.pull = pull

    @typ.override
    def pull_request(
        self, identity: stack_records.PullRequestIdentity
    ) -> ParentPullRequest:
        """Return the one pull request this forge knows."""
        return self.pull

    @typ.override
    def pull_request_body(self, identity: stack_records.PullRequestIdentity) -> str:
        """Return an empty body, since the run reads bodies for stack parents."""
        return ""

    @typ.override
    def stack_parent(
        self, identity: stack_records.PullRequestIdentity
    ) -> stack_records.PullRequestIdentity | None:
        """Return ``None``: this pull request is not stacked on another."""
        return None

    @typ.override
    def associated_pull_requests(
        self, repository: str, commits: cabc.Sequence[str]
    ) -> wheresat_github.AssociationPage:
        """Return the page of a search that examined every commit and found none."""
        return examined_and_found_none(commits)


@dataclasses.dataclass(frozen=True, slots=True)
class Journey:
    """One repository a journey happens in, and the facts it is about.

    Attributes
    ----------
    scenario : WheresatScenario
        The checkout a run reads, holding the child branch and the commit its
        boundary was at.
    landed : str
        The parent's integration commit on the trunk, which a squash merge makes
        a new commit rather than the parent's own head.
    trunk_tip : str
        The trunk after its own advance past the squash, which is the commit a
        replay is onto.
    parent_head : str
        The head the parent pull request records, which origin holds and the
        fork journey's fork holds instead.
    inherited_head : str
        The parent's tip at the moment the child was cut from it. It is
        ``parent_head`` where the parent did not move afterwards, and an older
        commit the parent's history no longer reaches where it was rewritten.
    identity : stack_records.PullRequestIdentity
        The parent pull request, which is also the name a fetched head is cached
        under.
    forge : ScriptedForge
        What the run is handed in place of GitHub.
    where : Where
        Working tree a run is made from: the child's own worktree where ``git
        donkey`` made one, or the checkout the rewritten shape leaves the child
        checked out in.
    fork_path : Path | None
        Bare repository the fork's URL is rewritten to, where the journey has a
        fork.

    """

    scenario: WheresatScenario
    landed: str
    trunk_tip: str
    parent_head: str
    inherited_head: str
    identity: stack_records.PullRequestIdentity
    forge: ScriptedForge
    where: Where
    fork_path: Path | None = None

    @property
    def directory(self) -> Path:
        """The working tree a run over this journey is made from."""
        return working_tree(self.scenario, self.where)

    def run(
        self,
        options: wheresat.WheresatOptions,
        capsys: pytest.CaptureFixture[str],
    ) -> WheresatRun:
        """Run the command over this journey, with the forge it scripts.

        Parameters
        ----------
        options : wheresat.WheresatOptions
            What the command line asked for.
        capsys : pytest.CaptureFixture[str]
            Capture fixture the run's output is read from.

        Returns
        -------
        WheresatRun
            The exit status and both output streams.

        """
        return run_wheresat_at(self.directory, options, capsys, forge=self.forge)


def reading_of(journey: Journey) -> Fingerprint:
    """Return the fingerprint of every working tree the journey's run reads.

    A journey ``git donkey`` built has a linked worktree as well as its checkout,
    and a run could disturb either; the shapes rebuilt from the fixture have one
    checkout, and fingerprinting a tree that does not exist would read as an
    empty one and say nothing about what a run touched.

    Parameters
    ----------
    journey : Journey
        The journey whose working trees are read.

    Returns
    -------
    Fingerprint
        The readings, comparable with :meth:`Fingerprint.differences`.

    """
    scenario = journey.scenario
    if journey.where == "worktree":
        return reading(scenario)
    return fingerprint(scenario.local_path, repo=scenario.repo)


def branch_head(path: Path, branch: str) -> str:
    """Return the commit the branch ``branch`` names in the repository at ``path``.

    An absent branch is the empty string rather than the shared reader's
    ``None``, because the callers here compare the answer with the commits a
    journey recorded and never ask whether the branch existed.

    Returns
    -------
    str
        The commit, or ``""`` when the repository has no such branch.

    """
    commit = git_repo_helpers.ref_value(Repo(path), f"refs/heads/{branch}")
    return "" if commit is None else commit


def _bare_repository(root: Path) -> Path:
    """Create a bare repository whose default branch is the trunk.

    Returns
    -------
    Path
        The repository's path, which is also what a URL is rewritten to.

    """
    root.mkdir(parents=True, exist_ok=True)
    repo = Repo.init(root, bare=True)
    repo.git.symbolic_ref("HEAD", "refs/heads/main")
    return root


def _publish(repo: Repo) -> None:
    """Fetch from ``origin`` and name its default branch in the checkout."""
    repo.git.fetch("origin")
    repo.git.remote("set-head", "origin", "main")


def _name_github_repository(repo: Repo, remote: str, slug: str, path: Path) -> None:
    """Point ``remote`` at ``slug``, and let its traffic reach ``path``.

    The command reads a remote's URL to learn which GitHub repository it names,
    and reads the rewrite below only by fetching: naming the repository and
    reaching a repository the suite can see are two different configuration
    keys, and every journey here needs both.

    Parameters
    ----------
    repo : git.Repo
        Repository whose remote is configured.
    remote : str
        Name of the remote to configure.
    slug : str
        ``OWNER/REPOSITORY`` the remote's URL names.
    path : Path
        Repository the URL is rewritten to, which the fetch then reaches.

    """
    url = f"https://github.com/{slug}.git"
    repo.git.config(f"remote.{remote}.url", url)
    repo.git.config(f"url.{path.as_posix()}.insteadOf", url)


def _pull(
    repo: Repo,
    landed: str,
    parent_head: str,
    *,
    head_repository: str = REPOSITORY,
) -> ParentPullRequest:
    """Return the parent pull request a journey's forge answers with.

    Parameters
    ----------
    repo : git.Repo
        Repository the pull request is about.
    landed : str
        The squash commit the parent merged as.
    parent_head : str
        The head the pull request records.
    head_repository : str, optional
        Repository the head lives in, which is the fork for a fork's journey.

    Returns
    -------
    ParentPullRequest
        The metadata a run reads before fetching the head it names, with the
        provenance the fetch fills left unset: what a run reports about where
        the head came from is the adapter's answer, not the fixture's.

    """
    return ParentPullRequest(
        identity=stack_records.PullRequestIdentity(REPOSITORY, NUMBER),
        merged=True,
        merged_at=MERGED_AT,
        head_sha=parent_head,
        head_ref=PARENT,
        head_repository=head_repository,
        # The adapter has not fetched the head at this point.
        head_fetched_from=None,
        base_ref="main",
        base_repository=REPOSITORY,
        landed=landed,
        stacked=False,
    )


def squashed(root: Path) -> Journey:
    """Build the Background: a stacked child whose parent was squash-merged.

    The child's worktree is committed to before the squash, because a boundary
    with no work above it has no replay range to partition and the command would
    refuse it for a reason that has nothing to do with the merge.

    Parameters
    ----------
    root : Path
        Directory the checkout and its bare remote are created under.

    Returns
    -------
    Journey
        The Background, with the record ``git donkey`` wrote still in place.

    Raises
    ------
    AssertionError
        If the parent branch the squash merges is not in the repository the
        fixture just built, which would make the journey about a merge that
        never happened.

    """
    scenario = stacked_child(root)
    repo = scenario.repo
    parent_head = git_repo_helpers.ref_value(repo, f"refs/heads/{PARENT}")
    if parent_head is None:
        # The fixture built this branch a few lines above, so its absence is the
        # fixture being wrong about the repository rather than a journey shape.
        msg = f"the fixture's branch {PARENT} must exist to be merged"
        raise AssertionError(msg)
    worktree = scenario.worktree_repo()
    git_repo_helpers.commit_file(
        worktree,
        scenario.worktree_path() / _CHILD_WORK,
        "child work",
        "Child work",
    )
    tip = scenario.worktree_head()
    repo.git.checkout("main")
    repo.git.merge("--squash", PARENT)
    repo.git.commit("-m", "Squash-merge the parent")
    landed = repo.head.commit.hexsha
    trunk_tip = git_repo_helpers.advance(
        repo, message="Advance the trunk after the squash"
    )
    repo.git.push("origin", "main:main", f"{PARENT}:{PARENT}")
    _publish(repo)
    _name_github_repository(repo, "origin", REPOSITORY, scenario.remote_path)
    _expect(
        git_repo_helpers.is_ancestor(repo, landed, trunk_tip),
        "the squash commit must be on the trunk it merged into",
    )
    return Journey(
        scenario=dataclasses.replace(scenario, tip=tip),
        landed=landed,
        trunk_tip=trunk_tip,
        parent_head=parent_head,
        inherited_head=scenario.boundary,
        identity=stack_records.PullRequestIdentity(REPOSITORY, NUMBER),
        forge=ScriptedForge(_pull(repo, landed, parent_head)),
        where="worktree",
    )


def rewritten(root: Path) -> Journey:
    """Build the shape where the parent branch was rebased before it merged.

    The parent's commits are rebuilt with the same content and new object IDs,
    so the head the pull request records no longer reaches the commit the child
    was cut from: the boundary is still a fact about the child's history, and
    nothing in the repository attests it. The reflogs are expired, because the
    fixture's own history wrote them: without that, the branch reflogs would name
    the historical tip, and the journey would not be the record-less, reflog-less
    repository the feature describes.

    Parameters
    ----------
    root : Path
        Directory the checkout and its bare remote are created under.

    Returns
    -------
    Journey
        The rewritten shape, with no record and no surviving reflog.

    """
    repo_path = root / "rewritten"
    fixture = git_repo_helpers.rewritten_parent_stack(repo_path)
    remote_path = _bare_repository(root / "rewritten-remote")
    fixture.repo.create_remote("origin", remote_path.as_posix())
    fixture.repo.git.push("origin", "main:main", f"{PARENT}:{PARENT}")
    _publish(fixture.repo)
    _name_github_repository(fixture.repo, "origin", REPOSITORY, remote_path)
    fixture.repo.git.checkout(CHILD)
    fixture.repo.git.reflog("expire", "--expire=now", "--all")
    _expect(
        fixture.parent_head != fixture.inherited_head,
        "the rewritten parent's head must not be the head the child inherited",
    )
    scenario = WheresatScenario(
        local_path=repo_path,
        remote_path=remote_path,
        boundary=fixture.inherited_head,
        tip=fixture.child_tip,
        child=CHILD,
        parent=PARENT,
    )
    return Journey(
        scenario=scenario,
        landed=fixture.landed,
        trunk_tip=fixture.target,
        parent_head=fixture.parent_head,
        inherited_head=fixture.inherited_head,
        identity=stack_records.PullRequestIdentity(REPOSITORY, NUMBER),
        forge=ScriptedForge(_pull(fixture.repo, fixture.landed, fixture.parent_head)),
        where="checkout",
    )


def restored(root: Path) -> Journey:
    """Build the rewritten shape with a second commit at the landed tree.

    The child edits the content it inherited, restores it, and commits once more,
    which puts two of its commits at the landed tree with a commit above them.
    Both are proposed by content comparison, both clear every applicable gate,
    and neither can be preferred to the other — the state the feature's fourth
    scenario is about. A commit above the restoring one is what keeps the
    restoring commit from being the child's tip, which the replay-range gate
    would refuse.

    Parameters
    ----------
    root : Path
        Directory the checkout and its bare remote are created under.

    Returns
    -------
    Journey
        The rewritten shape, with the two matching commits in the child's history.

    """
    journey = rewritten(root)
    repo = journey.scenario.repo
    root_path = journey.scenario.local_path
    repo.git.rm(git_repo_helpers.CHILD_FILE)
    repo.git.commit("-m", _RESTORING)
    git_repo_helpers.commit_file(repo, root_path / _LATER, "later work", "Later work")
    return dataclasses.replace(
        journey,
        scenario=dataclasses.replace(journey.scenario, tip=repo.head.commit.hexsha),
    )


def forked(root: Path) -> Journey:
    """Build the Background with the parent pull request opened from a fork.

    The fork holds the parent branch and origin does not: the head the pull
    request records could only have been fetched from the fork, so a run that
    reaches it has followed the metadata rather than a guess about which
    repository to fetch from.

    Parameters
    ----------
    root : Path
        Directory the checkout and its bare remotes are created under.

    Returns
    -------
    Journey
        The Background, with the parent's head living in a fork.

    """
    journey = squashed(root)
    repo = journey.scenario.repo
    fork_path = _bare_repository(root / "fork")
    repo.create_remote("upstream", fork_path.as_posix())
    _name_github_repository(repo, "upstream", FORK, fork_path)
    repo.git.push("upstream", f"{PARENT}:{PARENT}", "main:main")
    repo.git.push("origin", f":refs/heads/{PARENT}")
    _expect(
        branch_head(fork_path, PARENT) == journey.parent_head,
        "the fork must hold the head the pull request records",
    )
    _expect(
        not branch_head(journey.scenario.remote_path, PARENT),
        "origin must not hold the parent branch any more",
    )
    return dataclasses.replace(
        journey,
        forge=ScriptedForge(
            _pull(repo, journey.landed, journey.parent_head, head_repository=FORK)
        ),
        fork_path=fork_path,
    )


def grafted(root: Path) -> Journey:
    """Build the Background and cut the repository's history at the child's tip.

    The child branch is pushed and then fetched at depth one, so the repository
    becomes shallow and the commit the record attests falls outside its history:
    the ancestry question the boundary turns on has no answer Git can give, which
    is what the feature's sixth scenario is about.

    Parameters
    ----------
    root : Path
        Directory the checkout and its bare remote are created under.

    Returns
    -------
    Journey
        The Background, with a shallow history and its record still in place.

    """
    journey = squashed(root)
    repo = journey.scenario.repo
    repo.git.push("origin", f"{CHILD}:{CHILD}")
    repo.git.fetch("--depth=1", "origin", CHILD)
    _expect(
        repo.git.rev_parse("--is-shallow-repository") == "true",
        "the fetch at depth one must leave the repository shallow",
    )
    _expect(
        journey.scenario.boundary
        not in repo.git.rev_list("--end-of-options", journey.scenario.tip).split(),
        "the boundary must fall outside the shallow history it was cut from",
    )
    return journey


def reflog_lines(scenario: WheresatScenario) -> tuple[str, ...]:
    """Return every reflog line of the scenario's repository.

    Returns
    -------
    tuple[str, ...]
        Git's ``reflog --all`` output, split into lines, or an empty tuple when
        the repository has no reflog left to read.

    """
    with contextlib.suppress(GitCommandError):
        return tuple(str(scenario.repo.git.reflog("--all")).splitlines())
    return ()
