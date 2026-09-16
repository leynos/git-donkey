"""Behaviour-driven tests for the journeys ``git wheresat`` answers.

``features/git_wheresat.feature`` is the specification this module binds: seven
journeys over a squash-merged parent, each built by
:mod:`tests.integration.wheresat_scenarios` as a real checkout and run through
the whole command line. The suite's job is to say what the command does for a
user standing in such a repository, so the ``Given`` steps assert the shape of
the repository the journey is about — that the history really is shallow, that
the rewritten parent really has lost the head the child inherited, that the fork
really is the only repository holding the parent's head — and the ``Then`` steps
assert what the run made of it, from the report it printed and from the
repository it left behind.

Two assertions are made against Git rather than against the report, because a
report is a claim and a repository is not. The fork journey checks the ref a
fetched head is cached under, which is written only after a fetch from the
remote the pull request's own metadata names, and the last journey compares a
fingerprint of the whole repository taken before the run with one taken after,
so a run that wrote anything but evidence of its own fails where it happened.
The rest of the suite reads the reports, and reads them as token sets rather
than as text, so column widths are not what any assertion is about.

The record is where several journeys differ, and it is read back out of Git
rather than assumed: the ``Given`` that requires one asserts the anchor ref and
the branch configuration both name the boundary, and the ``Given`` that requires
none removes both and asserts they are gone.
"""

from __future__ import annotations

import dataclasses
import re
import typing as typ

from pytest_bdd import given, parsers, scenarios, then, when

from git_donkey import stack_records, wheresat, wheresat_records, wheresat_refs
from tests import git_repo_helpers
from tests.integration import wheresat_helpers, wheresat_scenarios
from tests.integration.wheresat_helpers import (
    CHILD,
    EVIDENCE_NAMESPACE,
    Fingerprint,
    WheresatRun,
    anchor,
    configuration,
    reading,
    report_tokens,
)
from tests.integration.wheresat_scenarios import PULL_REQUEST, Journey

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest

_BACKUP_REF: typ.Final = wheresat_records.backup_ref(CHILD)
"""Ref a run advises creating before it replays the child's work."""

_REBASE: typ.Final = ("git", "rebase", "--onto")
"""First tokens of the replay command the report proposes."""

_INDISTINGUISHABLE: typ.Final = re.compile(
    r"(?P<count>\d+) commits cleared every applicable gate "
    r"\((?P<commits>[0-9a-f]+(?:, [0-9a-f]+)+)\) and are named by content "
    r"comparison alone, which cannot establish a boundary or choose between them\Z"
)
"""The reason a run gives when content comparison names two candidates."""

_ANCESTRY_UNKNOWN: typ.Final = re.compile(
    r"Git could not answer whether [0-9a-f]+ is an ancestor of [0-9a-f]+"
)
"""What an indeterminate ancestry check says, which is not a refusal of one."""

_NOT_AN_ANCESTOR: typ.Final = "is not an ancestor"
"""What a *failed* ancestry check says, and what an indeterminate one must not."""

_HEAD_ROW: typ.Final = ("attested", "pull-request-head")
"""First tokens of the row citing a fetched pull request head as evidence."""

_INFERRED_ROW: typ.Final = ("inferred", "tree-identity")
"""First tokens of a row citing a commit found by content comparison."""

_PARENT_HISTORY: typ.Final = "parent-history-intact"
"""Gate asking whether the candidate is an ancestor of the parent's head."""

_CANDIDATE_COUNT: typ.Final = 2
"""Commits the feature's two-candidate journey leaves carrying the landed tree."""

_RECORD_KEYS: typ.Final = tuple(key.value for key in stack_records.RecordKey)
"""Every configuration key a stack record is made of, without its prefix."""


@dataclasses.dataclass(slots=True)
class WheresatJourney:
    """The repository a scenario runs in, and what its runs made of it.

    Attributes
    ----------
    journey : Journey
        The checkout, its commits, and the forge the run is handed.
    untouched : Fingerprint
        The whole repository as the ``Given`` steps left it.
    runs : list[WheresatRun]
        What each ``When`` step reported, in the order they ran.

    """

    journey: Journey
    untouched: Fingerprint
    runs: list[WheresatRun] = dataclasses.field(default_factory=list)


def _reported(scenario: WheresatJourney) -> WheresatRun:
    """Return what the scenario's most recent run reported."""
    assert scenario.runs, "no run has been made yet"
    return scenario.runs[-1]


def _rows(run: WheresatRun, *leading: str) -> list[tuple[str, ...]]:
    """Return the report's lines whose first tokens are ``leading``.

    Parameters
    ----------
    run : WheresatRun
        The run whose standard output is read.
    leading : str
        Tokens the line must start with, compared as a whole so a row is
        identified by its fields rather than by where its columns fall.

    Returns
    -------
    list[tuple[str, ...]]
        One token tuple per matching line, deduplicated and in no particular
        order, because the rows are read out of a token set.

    """
    return [
        tokens
        for tokens in report_tokens(run.stdout)
        if tokens[: len(leading)] == leading
    ]


def _sole_boundary(scenario: WheresatJourney) -> str:
    """Return the one commit the report names as the replay boundary.

    A report that named two boundaries, or none, would not have answered the
    question it was asked, so the count is asserted before the commit is.

    Returns
    -------
    str
        The abbreviated commit, which the caller checks against the full ID it
        expects the report to have named.

    """
    run = _reported(scenario)
    lines = [
        line for line in run.stdout.splitlines() if line.split()[:1] == ["boundary"]
    ]

    assert len(lines) == 1, (
        f"expected exactly one replay boundary in the report, got {lines}:\n"
        f"{run.stdout}"
    )
    return lines[0].split()[1]


def _replay(scenario: WheresatJourney) -> tuple[str, ...]:
    """Return the replay command the report proposes, as its tokens."""
    rows = _rows(_reported(scenario), *_REBASE)

    assert len(rows) == 1, (
        f"expected exactly one replay command, got {rows}:\n"
        f"{_reported(scenario).stdout}"
    )
    return rows[0]


def _in_namespace(print_of: Fingerprint, namespace: str) -> list[str]:
    """Return the refs of ``print_of`` that live under ``namespace``.

    Returns
    -------
    list[str]
        The ``git for-each-ref`` lines for those refs, so two readings of one
        namespace are compared as the commits they name and not only as names.

    """
    return sorted(
        ref for ref in print_of.refs if ref.split(" ")[0].startswith(namespace)
    )


def _short(commit: str) -> str:
    """Return the abbreviation Git's own reports would print for ``commit``."""
    return commit[: wheresat_records.COMMIT_ABBREVIATION]


@given("a child branch stacked on a parent branch", target_fixture="scenario")
def child_stacked_on_a_parent(tmp_path: Path) -> WheresatJourney:
    """Create the Background: a stacked child whose parent was squash-merged.

    Returns
    -------
    WheresatJourney
        The Background journey, with the fingerprint INV-1 is measured against.

    """
    journey = wheresat_scenarios.squashed(tmp_path)
    untouched = wheresat_scenarios.reading_of(journey)

    return WheresatJourney(journey=journey, untouched=untouched)


@given("the parent pull request was squash-merged into the trunk")
def parent_was_squash_merged(scenario: WheresatJourney) -> None:
    """Check the squash the whole feature is about really happened.

    Two facts decide every journey here. The squash commit is on the trunk, so a
    replay onto the trunk lands the parent's content; and the parent's own head
    is *not* on the trunk, which is what makes the parent's head a boundary a
    child could have been cut from rather than an ordinary ancestor.
    """
    journey = scenario.journey
    repo = journey.scenario.repo

    assert git_repo_helpers.is_ancestor(repo, journey.landed, journey.trunk_tip), (
        "the squash commit must be reachable from the trunk"
    )
    assert not git_repo_helpers.is_ancestor(
        repo, journey.parent_head, journey.trunk_tip
    ), "the squash must not have merged the parent's own head into the trunk"
    assert git_repo_helpers.is_ancestor(
        repo, journey.inherited_head, journey.scenario.tip
    ), "the child must reach the head it inherited"


@given("a stack record naming the inherited boundary")
def a_record_naming_the_boundary(scenario: WheresatJourney) -> None:
    """Check the child's birth record names the commit the boundary was at.

    The record is read back out of Git — the anchor ref and the branch
    configuration both — because a run that found only one of them would not be
    reading the record this journey claims to have.
    """
    checkout = scenario.journey.scenario
    named = anchor(checkout)
    recorded = configuration(checkout).get(stack_records.RecordKey.BASE.value)

    assert named == checkout.boundary, (
        f"expected the anchor ref to name the boundary {checkout.boundary}, "
        f"but it names {named}"
    )
    assert recorded == checkout.boundary, (
        f"expected the record to state the boundary {checkout.boundary}, "
        f"but it states {recorded}"
    )


@given("no stack record")
def no_record(scenario: WheresatJourney) -> None:
    """Remove the child's record, and check nothing of it is left to be read."""
    checkout = scenario.journey.scenario
    wheresat_helpers.forget_record(checkout)
    remaining = [key for key in configuration(checkout) if key in _RECORD_KEYS]

    assert anchor(checkout) is None, "the anchor ref must be gone"
    assert not remaining, f"expected no record to be left behind, found {remaining}"


@given("the parent pull request head is an ancestor of the child branch")
def parent_head_is_an_ancestor(scenario: WheresatJourney) -> None:
    """Check the premise the pull request head establishes the boundary on.

    The head is the boundary only because the child was cut from it, so a
    journey where the head was not an ancestor would be a different scenario
    wearing this one's name.
    """
    journey = scenario.journey

    assert journey.forge.pull.head_sha == journey.parent_head, (
        "the forge must answer with the head this journey is about"
    )
    assert git_repo_helpers.is_ancestor(
        journey.scenario.repo, journey.parent_head, journey.scenario.tip
    ), "the pull request head must be an ancestor of the child branch"


def _replaced(scenario: WheresatJourney, journey: Journey) -> None:
    """Make ``journey`` the repository the scenario runs in.

    A later ``Given`` replaces the Background's repository rather than adjusting
    it, because the shapes differ in their history and not only in their state:
    a parent that was rewritten cannot be un-rewritten, and a repository whose
    history was cut short cannot be deepened.
    """
    scenario.journey = journey
    scenario.untouched = wheresat_scenarios.reading_of(journey)


@given("the parent branch was rebased before it was merged")
def parent_was_rebased(scenario: WheresatJourney, tmp_path: Path) -> None:
    """Replace the Background with the shape that lost the inherited head.

    The parent's commits are rebuilt from the trunk with the same content and
    new object IDs, so the child still reaches the head it was cut from while
    nothing the parent's history reaches attests it.
    """
    journey = wheresat_scenarios.rewritten(tmp_path / "rebased")
    _replaced(scenario, journey)
    repo = journey.scenario.repo

    assert not git_repo_helpers.is_ancestor(
        repo, journey.inherited_head, journey.parent_head
    ), "the rewritten parent must no longer reach the head the child inherited"
    assert git_repo_helpers.is_ancestor(
        repo, journey.inherited_head, journey.scenario.tip
    ), "the child must still reach the head it inherited"


@given("only content-comparison evidence remains")
def only_content_evidence(scenario: WheresatJourney) -> None:
    """Check that nothing but a content comparison can name the lost boundary.

    The record is gone and the rewritten parent does not reach the historical
    tip, so neither a record nor an ancestry question can propose it; the only
    rung left is the one that compares what commits carry.
    """
    journey = scenario.journey
    repo = journey.scenario.repo
    reaching = str(
        repo.git.for_each_ref(
            "--contains", journey.inherited_head, "--format=%(refname)"
        )
    ).split()

    assert anchor(journey.scenario) is None, "no record may name the boundary"
    assert reaching == [f"refs/heads/{CHILD}"], (
        f"expected the child's own history to be the only thing reaching "
        f"{journey.inherited_head}, but these refs do: {reaching}"
    )


@given("no surviving ref or reflog records the historical parent tip")
def no_ref_or_reflog_names_it(scenario: WheresatJourney) -> None:
    """Check the historical tip survives in no ref, and in no reflog either.

    A reflog would be enough for the command to read the boundary out of, so the
    journey is only about content comparison if the reflogs were expired along
    with the refs — which is what a repository that has been through Git's own
    collection looks like.
    """
    journey = scenario.journey
    logs = wheresat_scenarios.reflog_lines(journey.scenario)
    naming = [
        line
        for line in logs
        if line.split() and journey.inherited_head.startswith(line.split()[0])
    ]

    assert not naming, (
        f"expected no reflog line to name {journey.inherited_head}, got {naming}"
    )


@given("two distinct commits match the squashed parent change")
def two_commits_match(scenario: WheresatJourney, tmp_path: Path) -> None:
    """Replace the journey with the shape where two commits carry the landed tree.

    The child restores the content it inherited and commits once more, which
    leaves the inherited head and the restoring commit at the same tree with a
    commit above them. Two commits at one tree is what content comparison cannot
    choose between; the commit above is what keeps either from being the child's
    tip, which the replay range would refuse instead.
    """
    journey = wheresat_scenarios.restored(tmp_path / "restored")
    _replaced(scenario, journey)
    repo = journey.scenario.repo
    landed_tree = repo.git.rev_parse(f"{journey.landed}^{{tree}}")
    matching = [
        commit
        for commit in repo.git.rev_list(journey.scenario.tip).split()
        if repo.git.rev_parse(f"{commit}^{{tree}}") == landed_tree
    ]

    assert len(matching) == _CANDIDATE_COUNT, (
        f"expected exactly two commits at the landed tree, got {matching}"
    )
    assert journey.scenario.tip not in matching, (
        "the child tip must be above the two commits rather than one of them"
    )


@given("the parent pull request was opened from a fork of the child repository")
def parent_opened_from_a_fork(scenario: WheresatJourney, tmp_path: Path) -> None:
    """Replace the journey with one whose parent head only a fork holds.

    The parent branch is pushed to the fork and deleted from origin, so the head
    the pull request records cannot be fetched from origin at all: a run that
    reaches it has followed the metadata to the repository that has it.
    """
    journey = wheresat_scenarios.forked(tmp_path / "forked")
    _replaced(scenario, journey)
    fork_path = journey.fork_path

    assert fork_path is not None, "the fork journey must have a fork repository"
    assert journey.forge.pull.head_repository == wheresat_scenarios.FORK, (
        "the pull request must record its head as living in the fork"
    )
    assert wheresat_scenarios.branch_head(fork_path, journey.scenario.parent) == (
        journey.parent_head
    ), "the fork must hold the head the pull request records"
    assert not wheresat_scenarios.branch_head(
        journey.scenario.remote_path, journey.scenario.parent
    ), "origin must not hold the parent branch any more"


@given("the repository history is shallow")
def history_is_shallow(scenario: WheresatJourney, tmp_path: Path) -> None:
    """Replace the journey with one whose history is cut at the child's tip.

    The child branch is fetched at depth one, which leaves a repository whose
    traversal stops at the graft — so the commit the record attests is outside
    the history Git can walk, and the question the boundary turns on has no
    answer to give.
    """
    journey = wheresat_scenarios.grafted(tmp_path / "shallow")
    _replaced(scenario, journey)
    repo = journey.scenario.repo

    assert repo.git.rev_parse("--is-shallow-repository") == "true", (
        "the graft must leave the repository shallow"
    )
    assert anchor(journey.scenario) == journey.scenario.boundary, (
        "the record must still name the boundary the graft put out of reach"
    )


@when("I run git wheresat")
def run_wheresat(scenario: WheresatJourney, capsys: pytest.CaptureFixture[str]) -> None:
    """Run the command on the branch the journey's working tree holds."""
    scenario.runs.append(scenario.journey.run(wheresat.WheresatOptions(), capsys))


@when("I run git wheresat, naming the parent pull request")
def run_wheresat_naming_the_parent(
    scenario: WheresatJourney,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run the command with the parent pull request named on the command line."""
    options = wheresat.WheresatOptions(parent=PULL_REQUEST)

    scenario.runs.append(scenario.journey.run(options, capsys))


@when("I run git wheresat, naming the parent pull request, with deep scanning enabled")
def run_wheresat_naming_the_parent_deeply(
    scenario: WheresatJourney,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run the command naming the parent and asking for the content comparison."""
    options = wheresat.WheresatOptions(parent=PULL_REQUEST, deep=True)

    scenario.runs.append(scenario.journey.run(options, capsys))


@when("I run git wheresat with deep scanning enabled")
def run_wheresat_deeply(
    scenario: WheresatJourney,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run the command asking for the content comparison, naming no parent."""
    options = wheresat.WheresatOptions(deep=True)

    scenario.runs.append(scenario.journey.run(options, capsys))


@then("the report names the recorded commit as the exclusive replay boundary")
def boundary_is_the_recorded_commit(scenario: WheresatJourney) -> None:
    """Check the boundary the report answers with is the commit the record states."""
    checkout = scenario.journey.scenario
    boundary = _sole_boundary(scenario)

    assert checkout.boundary.startswith(boundary), (
        f"expected the report's boundary {boundary} to be the commit the record "
        f"attests, {checkout.boundary}"
    )
    assert _replay(scenario)[4] == checkout.boundary, (
        "the replay command must name the recorded commit in full"
    )


@then("the report proposes a backup ref and a rebase command with full object IDs")
def replay_is_fully_specified(scenario: WheresatJourney) -> None:
    """Check the report offers a replay the user can run without guessing.

    The backup ref is what makes the replay undoable, and both object IDs are
    named in full: an abbreviated ID in a command a user is asked to paste is a
    command that may stop meaning what the report meant.
    """
    checkout = scenario.journey.scenario
    tokens = report_tokens(_reported(scenario).stdout)
    replay = _replay(scenario)

    assert ("git", "update-ref", _BACKUP_REF, checkout.tip) in tokens, (
        f"expected the report to advise backing the child tip up as {_BACKUP_REF}"
    )
    assert replay[3] == scenario.journey.trunk_tip, (
        "the replay must name the commit it replays onto in full"
    )
    assert replay[4] == checkout.boundary, (
        "the replay must name the commit it replays from in full"
    )
    assert replay[5] == CHILD, "and the branch it replays"


@then("the report names the pull request head as the exclusive replay boundary")
def boundary_is_the_pull_request_head(scenario: WheresatJourney) -> None:
    """Check the boundary is the head the pull request records."""
    head = scenario.journey.parent_head
    boundary = _sole_boundary(scenario)

    assert head.startswith(boundary), (
        f"expected the report's boundary {boundary} to be the pull request head {head}"
    )
    assert _replay(scenario)[4] == head, (
        "the replay command must name the pull request head in full"
    )


@then("the report cites pull request head ancestry as the establishing evidence")
def evidence_is_the_pull_request_head(scenario: WheresatJourney) -> None:
    """Check the report says the head is what established the boundary.

    The head is evidence because the child was cut from it, so the row the run
    cites it in is the report's account of the ancestry the boundary rests on.
    """
    head = scenario.journey.parent_head
    expected = (*_HEAD_ROW, _short(head), "the", "head", "of", PULL_REQUEST)
    tokens = report_tokens(_reported(scenario).stdout)

    assert expected in tokens, (
        f"expected the report to cite {head} as {PULL_REQUEST}'s head, got:\n"
        f"{_reported(scenario).stdout}"
    )


@then("the report names the parent-history-intact gate as the reason")
def parent_history_gate_failed(scenario: WheresatJourney) -> None:
    """Check the report fails the gate that asks whether the parent's history survived.

    The gate is read with the line under it, because the row says only which gate
    failed while the line under it says what it refused: a candidate the parent's
    rewritten head cannot reach, which the report also lists as evidence found by
    content comparison alone. Asserting both is what ties the refusal to the
    commit the run weighed rather than to the gate's name.
    """
    lines = _reported(scenario).stdout.splitlines()
    names = [line.split()[:2] for line in lines]
    failed = ["failed", _PARENT_HISTORY]

    assert failed in names, (
        f"expected {_PARENT_HISTORY} to be reported as failed, got:\n"
        f"{_reported(scenario).stdout}"
    )
    position = names.index(failed) + 1

    assert position < len(lines), (
        f"expected a detail line under {_PARENT_HISTORY}, got:\n"
        f"{_reported(scenario).stdout}"
    )
    detail = lines[position]
    refused = detail.split()[0]

    assert _NOT_AN_ANCESTOR in detail, (
        f"expected the failure to be an ancestry the parent does not have, "
        f"got: {detail}"
    )
    assert ("inferred", "tree-identity", refused) in [
        tokens[:3] for tokens in report_tokens(_reported(scenario).stdout)
    ], f"expected {refused} to be cited as content-comparison evidence"


@then("the report proposes no rebase command")
def no_replay_is_proposed(scenario: WheresatJourney) -> None:
    """Check a report that established nothing offers nothing to run.

    A refusal that printed a command would be read as an answer by anyone who
    ran it, so the absence is asserted as strictly as the command's presence is
    asserted where the run did establish a boundary.
    """
    run = _reported(scenario)

    assert not _rows(run, *_REBASE), (
        f"a refusal must not propose a replay:\n{run.stdout}"
    )
    assert "update-ref" not in run.stdout, "nor advise a ref to back the child up to"
    assert not _rows(run, "boundary"), f"nor name a boundary:\n{run.stdout}"


@then("the report lists both candidates with their evidence tier")
def both_candidates_are_listed(scenario: WheresatJourney) -> None:
    """Check the report says which commits matched, and on what kind of evidence.

    Two rows are required rather than one, because the whole point of the state
    is that content comparison named two commits and could not choose between
    them; a report that listed one would be answering a question it cannot.
    """
    rows = _rows(_reported(scenario), *_INFERRED_ROW)
    commits = {tokens[2] for tokens in rows}

    assert len(rows) >= _CANDIDATE_COUNT, (
        f"expected two candidates at the same evidence tier, got {rows}:\n"
        f"{_reported(scenario).stdout}"
    )
    assert len(commits) == len(rows), (
        f"expected the candidates to be distinct commits, got {rows}"
    )


@then("the report states the unresolved distinction between them")
def the_distinction_is_stated(scenario: WheresatJourney) -> None:
    """Check the report says it cannot choose, and names the commits it could not.

    The reason is matched as a whole line so it cannot be satisfied by a report
    that mentions an unresolved state somewhere else, and the commits it names
    are required to be the commits the evidence table listed.
    """
    run = _reported(scenario)
    stated = [
        match
        for line in run.stdout.splitlines()
        if (match := _INDISTINGUISHABLE.match(line.split("  - ", 1)[-1].strip()))
    ]
    listed = {tokens[2] for tokens in _rows(run, *_INFERRED_ROW)}

    assert stated, f"expected the report to state the distinction, got:\n{run.stdout}"
    assert len(stated) == 1, f"expected one such statement, got {stated}"
    assert set(stated[0].group("commits").split(", ")) == listed, (
        "the distinction must be about the candidates the report listed"
    )


@then("the pull request head is fetched from the fork rather than from origin")
def the_head_is_fetched_from_the_fork(scenario: WheresatJourney) -> None:
    """Check the run fetched the head, into the ref its metadata names.

    The cache ref is written only after a fetch from the remote that names the
    head's repository, and origin does not hold the parent branch at all — so a
    ref naming the head is a fetch from the fork and could not be anything else.
    """
    journey = scenario.journey
    cached = wheresat_refs.parent_head_ref(journey.identity)
    repo = journey.scenario.repo
    named = str(repo.git.rev_parse("--verify", "--quiet", cached) or "")

    assert named == journey.parent_head, (
        f"expected the fetched head to be cached at {cached}, which names {named}"
    )
    fork_path = journey.fork_path

    assert fork_path is not None, "the fork journey must have a fork repository"
    held = wheresat_scenarios.branch_head(fork_path, journey.scenario.parent)

    assert held == named, "the cached head must be the one the fork holds"


@then("the report states that the ancestry check was indeterminate")
def ancestry_is_indeterminate(scenario: WheresatJourney) -> None:
    """Check the report says Git could not answer, rather than answering no.

    The check is named as indeterminate in the gate table and the reason it could
    not be answered is printed under it, so both halves of the report are read.
    """
    run = _reported(scenario)

    assert ("indeterminate", "boundary-is-ancestor-of-child") in report_tokens(
        run.stdout
    ), f"expected an indeterminate ancestry check, got:\n{run.stdout}"
    assert any(_ANCESTRY_UNKNOWN.search(line) for line in run.stdout.splitlines()), (
        f"expected the report to say Git could not answer, got:\n{run.stdout}"
    )


@then("the report does not state that the boundary is not an ancestor")
def ancestry_is_not_denied(scenario: WheresatJourney) -> None:
    """Check an unanswerable question was not reported as a negative answer.

    This is the pair to the check above: a run that could not tell where the
    boundary was, but said the boundary was not an ancestor, would have turned
    its own uncertainty into a claim about the repository.
    """
    run = _reported(scenario)

    assert _NOT_AN_ANCESTOR not in run.stdout, (
        f"expected no denied ancestry, got:\n{run.stdout}"
    )


@then("no branch, tag, remote-tracking ref, index entry, or tracked file changes")
def nothing_but_evidence_changed(scenario: WheresatJourney) -> None:
    """Check the whole repository is as the ``Given`` left it, evidence aside.

    The comparison is the shared fingerprint's, which is wider than the step:
    the index and both working trees, the tracked and untracked files, the
    stash, the local configuration, ``FETCH_HEAD``, and every ref outside the
    evidence namespace the command is allowed to add to. The named ref
    namespaces are asserted as well, so a failure says which kind of ref moved.
    """
    before = scenario.untouched
    after = reading(scenario.journey.scenario)
    differences = before.differences(after)

    assert not differences, (
        f"expected the run to change nothing but evidence of its own, but the "
        f"repository moved: {differences}"
    )
    for namespace in ("refs/heads/", "refs/tags/", "refs/remotes/"):
        assert _in_namespace(before, namespace) == _in_namespace(after, namespace), (
            f"expected no ref under {namespace} to move"
        )


@then("the only new refs are under the evidence namespace")
def only_evidence_refs_are_new(scenario: WheresatJourney) -> None:
    """Check the refs the run added are evidence, and that it really added some.

    The fetch this journey asks for writes one durable ref caching the head it
    fetched, so the allowance is asserted to have been used: a run that wrote
    nothing would satisfy a claim about where its writes went without saying
    anything about the run.
    """
    before = set(scenario.untouched.refs)
    added = sorted(set(reading(scenario.journey.scenario).refs) - before)

    assert added, "expected the run's fetch to leave a ref behind"
    assert all(ref.split()[0].startswith(EVIDENCE_NAMESPACE) for ref in added), (
        f"expected every new ref to be evidence, got {added}"
    )


@then(parsers.parse("the command exits with status {status:d}"))
def the_command_exits_with_status(scenario: WheresatJourney, status: int) -> None:
    """Check that the run reported ``status``."""
    run = _reported(scenario)

    assert run.exit_code == status, (
        f"expected the run to exit with status {status}, not {run.exit_code}: "
        f"{run.stderr.strip()}"
    )


scenarios("features/git_wheresat.feature")
