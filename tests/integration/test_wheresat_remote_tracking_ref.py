"""The third rung of the parent-head ladder: a branch's remote-tracking ref.

``git wheresat`` seeks a ``PARENT_HEAD`` for the gates that ask about one. The
head the run fetched from the parent pull request answers first, the tombstone
``git plonk`` left behind answers second, and this suite is about the rung below
both: the ref a fetch of the parent branch itself left behind. That rung is what
is left for a parent that was never plonked and a child whose record names no
parent pull request, and it is what makes the parent-history gate applicable —
with no ``PARENT_HEAD`` at all that gate is not applicable, and the case this
command exists for, a parent rewritten after the child was stacked, goes
unnoticed.

The port-level tests ask a real repository, built by the shared fixtures, what
the rung answers: the ref a push and a fetch create, the absence a branch nobody
pushed has, the spellings a record may have written in its place, and the fault
a ref Git cannot read is. The command-level tests run the whole command line
from the child's worktree through the local forge double, so what they measure
is the report a user would see rather than the port's answer on its own.
"""

from __future__ import annotations

import dataclasses
import typing as typ
from pathlib import Path

import pytest
from git import Repo

from git_donkey import wheresat
from git_donkey.wheresat_errors import WheresatGraphError
from git_donkey.wheresat_graph import GitWheresatGraph
from git_donkey.wheresat_records import COMMIT_ABBREVIATION
from tests import git_repo_helpers
from tests.integration.conftest import _setup_repo
from tests.integration.plonk_helpers import branch_ahead_of_trunk
from tests.integration.wheresat_helpers import (
    Status,
    WheresatScenario,
    report_tokens,
    run_wheresat_in,
    stacked_child,
)

pytestmark = pytest.mark.timeout(120)

_PUSHED: typ.Final = "pushed"
"""Branch the port-level fixtures push, so a remote-tracking ref names it."""

_UNPUSHED: typ.Final = "unpushed"
"""Branch the port-level fixtures leave local, so no remote-tracking ref does."""


@dataclasses.dataclass(frozen=True, slots=True)
class _Tracking:
    """A checkout with one branch pushed and one branch nobody pushed.

    Attributes
    ----------
    repo : Repo
        The working checkout the branches were created in.
    pushed_tip : str
        Commit the pushed branch was left at, and the commit its
        remote-tracking ref therefore names.
    unpushed_tip : str
        Commit the branch nobody pushed was left at.

    """

    repo: Repo
    pushed_tip: str
    unpushed_tip: str


@pytest.fixture
def tracking(tmp_path: Path) -> _Tracking:
    """Build a checkout holding a pushed branch and a local-only one.

    The remote-tracking ref is created by pushing and fetching rather than by
    writing it, because the ref the rung returns is supposed to be Git's own
    answer about this repository: a test that wrote the ref would be checking
    the port against its author's idea of how Git spells one.

    Returns
    -------
    _Tracking
        The checkout and the two branches' tips.

    """
    local_path, _remote_path = _setup_repo(tmp_path)
    pushed_tip = branch_ahead_of_trunk(local_path, _PUSHED)
    unpushed_tip = branch_ahead_of_trunk(local_path, _UNPUSHED)
    repo = Repo(local_path)
    repo.git.push("origin", _PUSHED)
    repo.git.fetch("origin")
    return _Tracking(repo=repo, pushed_tip=pushed_tip, unpushed_tip=unpushed_tip)


def test_a_pushed_branch_names_its_remote_tracking_ref(tracking: _Tracking) -> None:
    """The ref a push and a fetch leave behind is the one the rung answers with.

    The expected name is computed from the branch in Git's own spelling rather
    than from the port: ``refs/remotes/<remote>/<branch>`` is what a fetch
    writes, and the assertion that the ref names the pushed tip is what shows
    the answer is about this branch rather than a name that happened to exist.
    """
    ref = f"refs/remotes/origin/{_PUSHED}"
    port = GitWheresatGraph(tracking.repo)

    assert port.remote_tracking_ref(_PUSHED) == ref, (
        "a branch that was pushed and fetched back has a remote-tracking ref"
    )
    assert tracking.repo.rev_parse(ref).hexsha == tracking.pushed_tip, (
        "and the ref the port named is the one Git created for the pushed tip"
    )


def test_a_branch_nobody_pushed_has_no_remote_tracking_ref(
    tracking: _Tracking,
) -> None:
    """A branch with no remote-tracking ref is an answer, not a fault.

    The branch itself exists, so the absence is about the ref and not about the
    revision: ``None`` says no candidate names a commit, which is what a parent
    that was never pushed deserves to be told.
    """
    port = GitWheresatGraph(tracking.repo)

    assert _UNPUSHED in tracking.repo.heads, "the branch itself exists locally"
    assert port.remote_tracking_ref(_UNPUSHED) is None, (
        "a branch nobody pushed has no remote-tracking ref"
    )
    assert _UNPUSHED not in tracking.repo.git.for_each_ref("refs/remotes/"), (
        "which is the whole reason the rung answers with nothing"
    )


def test_a_full_ref_path_is_answered_with_itself(tracking: _Tracking) -> None:
    """A record that kept a full ref path names its own ref, and that is the answer.

    The name is the first candidate, so it is returned as it stands whether it
    is the remote-tracking ref the record wrote or the local branch a record
    from another shape names: the rung is asked what a branch is called, and a
    name that is already a ref path is that answer without further guessing.
    """
    port = GitWheresatGraph(tracking.repo)
    remote_ref = f"refs/remotes/origin/{_PUSHED}"
    local_ref = f"refs/heads/{_PUSHED}"

    assert port.remote_tracking_ref(remote_ref) == remote_ref, (
        "a full ref path names itself, without a remote to guess at"
    )
    assert port.remote_tracking_ref(local_ref) == local_ref, (
        "a full ref path is not rewritten into a remote-tracking one"
    )


def test_a_short_remote_form_names_the_tracking_ref(tracking: _Tracking) -> None:
    """A record that wrote ``origin/<branch>`` still names the ref it meant."""
    assert (
        GitWheresatGraph(tracking.repo).remote_tracking_ref(f"origin/{_PUSHED}")
        == f"refs/remotes/origin/{_PUSHED}"
    ), "a short remote form names the ref the remote left behind"


def test_the_short_remote_form_outranks_a_remote_s_own_ref(
    tracking: _Tracking,
) -> None:
    """The candidates are asked in order, and the short form is second.

    Both spellings exist here and name different commits, so which one comes
    back says which candidate Git was asked about first. The ref under
    ``refs/remotes/`` is not one Git creates, but a record can name it, and a
    rung that skipped past it to a configured remote would answer about a
    different ref than the record asked about.
    """
    short = f"refs/remotes/{_PUSHED}"
    tracking.repo.git.update_ref(short, tracking.unpushed_tip)
    port = GitWheresatGraph(tracking.repo)

    assert tracking.repo.rev_parse(short).hexsha == tracking.unpushed_tip, (
        "the short form exists and names the other branch's commit"
    )
    assert (
        tracking.repo.rev_parse(f"refs/remotes/origin/{_PUSHED}").hexsha
        == tracking.pushed_tip
    ), "and the remote's own ref for the branch names a different commit"
    assert port.remote_tracking_ref(_PUSHED) == short, (
        "the short form is asked before a configured remote's ref"
    )


def test_the_remote_refs_are_asked_in_git_s_configuration_order(
    tmp_path: Path,
) -> None:
    """Configured remotes are tried in config order, not in sorted order.

    Two remotes are configured in an order that is not alphabetical and both
    hold a ref for the branch, so an answer that came back from the
    alphabetically first remote would be the answer the repository did not
    configure first. ``origin`` is configured by the shared fixture but holds
    no ref for this branch, so the two added here are the ones in question.
    """
    local_path, _remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)
    pushed_tip = branch_ahead_of_trunk(local_path, _PUSHED)
    unpushed_tip = branch_ahead_of_trunk(local_path, _UNPUSHED)
    repo.git.remote("add", "zeta", (tmp_path / "zeta.git").as_posix())
    repo.git.remote("add", "alpha", (tmp_path / "alpha.git").as_posix())
    repo.git.update_ref(f"refs/remotes/zeta/{_PUSHED}", pushed_tip)
    repo.git.update_ref(f"refs/remotes/alpha/{_PUSHED}", unpushed_tip)

    assert repo.rev_parse(f"refs/remotes/alpha/{_PUSHED}").hexsha == unpushed_tip, (
        "the alphabetically first remote's ref exists and names another commit"
    )
    assert GitWheresatGraph(repo).remote_tracking_ref(_PUSHED) == (
        f"refs/remotes/zeta/{_PUSHED}"
    ), "the remotes are asked in the order the repository configures them"


def test_a_ref_git_cannot_read_is_a_fault_and_not_an_absence(
    tracking: _Tracking,
) -> None:
    """A read that fails is reported, never handed back as "no such ref".

    Git answers a missing or unreadable ref with a quiet status and a corrupt
    object with a failure, and only the first of those is an absence (INV-5).
    The commit is corrupted rather than a ref file because that is the failure
    Git reports with a status of its own: the loose object is what the quiet
    question has to parse before it can answer.
    """
    object_id = tracking.repo.rev_parse(f"refs/remotes/origin/{_PUSHED}").hexsha
    objects = Path(tracking.repo.git.rev_parse("--absolute-git-dir")) / "objects"
    loose = objects / object_id[:2] / object_id[2:]
    assert loose.is_file(), "the pushed commit must be a loose object to corrupt"
    loose.chmod(0o644)
    loose.write_bytes(b"not the object Git wrote")

    with pytest.raises(WheresatGraphError) as refusal:
        GitWheresatGraph(tracking.repo).remote_tracking_ref(_PUSHED)

    assert "cannot tell whether" in str(refusal.value), (
        f"the failure is reported as an unanswered question, got: {refusal.value}"
    )
    assert f"refs/remotes/origin/{_PUSHED}" in str(refusal.value), (
        "and it names the candidate Git could not read"
    )


def _publish_parent(scenario: WheresatScenario) -> None:
    """Push the parent branch and fetch it back, so Git tracks it locally.

    Parameters
    ----------
    scenario : WheresatScenario
        The checkout whose parent branch is published.

    """
    scenario.repo.git.push("origin", scenario.parent)
    scenario.repo.git.fetch("origin")


def _rewrite_parent(scenario: WheresatScenario) -> None:
    """Rebuild the parent's tip from its own tree, then force-push and fetch it.

    ``git commit-tree`` writes a commit carrying the parent's tree with the
    trunk as its parent, so the branch names a commit the child does not reach:
    the rewrite an amend or a rebase before the merge leaves behind, with the
    content kept and the object ID changed. The force-push and the fetch are
    what leave the remote-tracking ref naming the rewritten commit.

    Parameters
    ----------
    scenario : WheresatScenario
        The checkout whose parent branch is rewritten.

    """
    repo = scenario.repo
    tip = repo.heads[scenario.parent].commit.hexsha
    trunk = repo.heads["main"].commit.hexsha
    tree = repo.git.rev_parse(f"{tip}^{{tree}}")
    rebuilt = str(
        repo.git.commit_tree(tree, "-p", trunk, "-m", "Rework the parent commit")
    ).strip()
    repo.git.update_ref(f"refs/heads/{scenario.parent}", rebuilt)
    repo.git.push("--force", "origin", scenario.parent)
    repo.git.fetch("origin")


@pytest.fixture
def intact(tmp_path: Path) -> WheresatScenario:
    """Build a stacked child whose parent is pushed and left where it was.

    Returns
    -------
    WheresatScenario
        The scenario, with ``refs/remotes/origin/<parent>`` naming the parent
        tip the child's record attests.

    """
    scenario = stacked_child(tmp_path)
    _publish_parent(scenario)
    return scenario


@pytest.fixture
def rewritten(tmp_path: Path) -> WheresatScenario:
    """Build a stacked child whose parent was rewritten after it was stacked.

    Returns
    -------
    WheresatScenario
        The scenario, with the remote-tracking ref naming the rewritten tip and
        the record still attesting the commit the child was cut from.

    """
    scenario = stacked_child(tmp_path)
    _publish_parent(scenario)
    _rewrite_parent(scenario)
    return scenario


def test_the_remote_tracking_ref_gives_the_parent_gate_a_head(
    intact: WheresatScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The rung is what makes the parent-history gate applicable, and it passes.

    The parent was pushed and did not move afterwards, so the ref the rung reads
    names exactly the commit the record attests: the boundary the report serves
    is the recorded one and the parent-history gate passes for it. The gate
    table is read with ``--explain`` because an established verdict prints it
    only when asked; without this rung there would be no ``PARENT_HEAD`` at all
    and the row would read not applicable instead of passed.
    """
    run = run_wheresat_in(intact, wheresat.WheresatOptions(explain=True), capsys)
    lines = report_tokens(run.stdout)

    assert run.exit_code == Status.ESTABLISHED, run.stderr
    assert ("boundary", intact.boundary[:COMMIT_ABBREVIATION]) in lines, (
        f"the recorded boundary is served, got:\n{run.stdout}"
    )
    assert ("not", "applicable", "parent-history-intact") not in lines, (
        "the rung found a head, so the gate is not reported as not applicable"
    )
    assert ("passed", "parent-history-intact") in lines, (
        f"and it passes for the attested boundary, got:\n{run.stdout}"
    )


def test_a_rewritten_parent_is_not_served_as_the_boundary(
    rewritten: WheresatScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The rung finds a head, and the recorded boundary stops being the answer.

    The remote-tracking ref names the rewritten commit, which the child does not
    reach, so the parent-history gate refutes the record's attested candidate:
    the run is applicable and the recorded boundary is not what it serves. No
    verdict is pinned here, because which candidate the run answers with instead
    is gate 7's business — ``replay-range-excludes-landed-work`` is the gate that
    refuses a boundary with landed work in the replay range, and
    ``_landed_work_is_in_scope`` needs a landed commit, which a local run with
    no resolved parent pull request does not have. EP-M6 measured that a best
    common ancestor is always an ancestor of both commits it was computed from,
    so the parent-history gate cannot refute a merge-base candidate and is not
    the rejection point for an advanced or rewritten parent.
    """
    repo = rewritten.repo
    ref = f"refs/remotes/origin/{rewritten.parent}"
    remote_tip = repo.rev_parse(ref).hexsha

    assert remote_tip != rewritten.boundary, "the parent must have moved"
    assert repo.heads[rewritten.parent].commit.hexsha == remote_tip, (
        "the local branch was rewritten too, so the push had something to send"
    )
    assert not git_repo_helpers.is_ancestor(repo, rewritten.boundary, remote_tip), (
        "the rewrite must put the recorded boundary out of the parent's reach"
    )

    run = run_wheresat_in(rewritten, wheresat.WheresatOptions(explain=True), capsys)
    lines = report_tokens(run.stdout)

    assert ("not", "applicable", "parent-history-intact") not in lines, (
        f"the rung found the rewritten head, so the gate is applicable, got:\n"
        f"{run.stdout}"
    )
    assert ("boundary", rewritten.boundary[:COMMIT_ABBREVIATION]) not in lines, (
        f"the refuted candidate is not served as the boundary, got:\n{run.stdout}"
    )
