"""INV-1: a read-only run leaves every part of the repository as it found it.

This is the command's headline promise, so it is measured rather than described.
Each vector of an explicit matrix runs the command line from inside a checkout —
the repository is the current directory's, as the console script finds it — and a
fingerprint taken before and after the run is compared over every ref and the
commit it names, the index and working tree of both working trees, the stash, the
local configuration, and ``FETCH_HEAD``.

The matrix covers the flags a read-only run accepts and the ways a run can be
refused: the default, ``--explain``, ``--no-fetch``, ``--offline``, ``--deep``,
``--json``, an ``--op-id`` naming a run, a named branch that resolves and one
that does not, an ``--onto`` that does not resolve, a named parent pull request
that cannot be consulted, and seven ``--op-id`` values that must be refused
before anything is built from them — one that escapes the namespace, one Git
would read as an option, one carrying a colon, one carrying a newline, and three
that are outside the alphabet's own arrangement of dots: an id holding ``..``,
one ending in ``.``, and one ending in ``.lock``. Every one of them leaves the
repository alone, including the vectors that fail: a run that refuses has no more
licence to tidy up than one that answers.

Two cases are larger than a flag: a worktree with uncommitted changes and a
worktree stopped in the middle of a rebase. Both are states the report warns
about, both must be left exactly as they were found, and each is built by its own
fixture because the matrix's checkout is shared by every vector.

The rest of the file is what stops the matrix from passing vacuously. One test
asserts the paths the vectors reach, from the observations a run records rather
than from the exit codes alone: the attested record an answering run reads, the
parent it asks for and cannot reach, the assessment a refusal never reaches, the
inferred-tier comparison only ``--deep`` asks for, and the fetch a named parent
pull request drives, once allowed and once refused.

That the fingerprint itself is sensitive — by six deliberate edits, each aimed
at one reading, and by the single difference INV-1 permits, a ref under
``refs/wheresat/`` — is measured in ``test_wheresat_fingerprint.py``, which
calibrates the instrument this file reads repositories with.

The fetch and the comparison are held to the paths they control rather than to
the flags they carry, so a run that never entered either fails where it did not.
Both are measured against a journey whose forge answers and whose cache is cold,
because the head a fetch writes is cached: a warm cache answers the
``--no-fetch`` run too, and the flag under test would be invisible.
"""

from __future__ import annotations

import dataclasses
import typing as typ
from pathlib import Path

import pytest

from git_donkey import observability, wheresat, wheresat_records, wheresat_refs
from tests.integration import wheresat_scenarios
from tests.integration.wheresat_helpers import (
    CHILD,
    PARENT,
    Fingerprint,
    WheresatRun,
    WheresatScenario,
    fingerprint,
    in_directory,
    run_wheresat,
    stacked_child,
)

if typ.TYPE_CHECKING:
    from git import Repo

    from tests.integration.wheresat_scenarios import Journey
    from tests.observability_helpers import RecordingRecorder

pytestmark = pytest.mark.timeout(120)

_TRACKED: typ.Final = "README.md"
"""Tracked file the dirt and the conflicting edits are made to."""

_FETCH_HEAD: typ.Final = "FETCH_HEAD"
"""File a fetch writes, which the run must not touch either."""

_ESTABLISHED: typ.Final = 0
_REFUSED: typ.Final = 1
_UNUSABLE: typ.Final = 2
_INDETERMINATE: typ.Final = 3

type _Where = typ.Literal["worktree", "checkout"]
"""Which working tree a vector is run from."""


@dataclasses.dataclass(frozen=True, slots=True)
class Vector:
    """One argument vector, and what the command must answer it with.

    Attributes
    ----------
    label : str
        Name of the vector, which is how a failing example identifies itself.
    options : wheresat.WheresatOptions
        The command line the vector asks for.
    exit_code : int
        Status the run must return. It is pinned here because a vector that
        refused for an unexpected reason would still leave the repository alone,
        and the matrix would then pass while testing nothing.
    where : _Where
        Working tree to run from: the child's worktree, where the branch is
        checked out and no ``--branch`` is needed, or the main checkout, where
        the branch has to be named.

    """

    label: str
    options: wheresat.WheresatOptions
    exit_code: int
    where: _Where = "worktree"


_VECTORS: typ.Final[typ.Mapping[str, Vector]] = {
    "default": Vector("default", wheresat.WheresatOptions(), _ESTABLISHED),
    "explain": Vector("explain", wheresat.WheresatOptions(explain=True), _ESTABLISHED),
    "no-fetch": Vector(
        "no-fetch", wheresat.WheresatOptions(no_fetch=True), _ESTABLISHED
    ),
    "offline": Vector("offline", wheresat.WheresatOptions(offline=True), _ESTABLISHED),
    "deep": Vector("deep", wheresat.WheresatOptions(deep=True), _ESTABLISHED),
    "json": Vector("json", wheresat.WheresatOptions(json=True), _ESTABLISHED),
    "op-id": Vector(
        "op-id", wheresat.WheresatOptions(op_id="read-only-run"), _ESTABLISHED
    ),
    "branch-named": Vector(
        "branch-named",
        wheresat.WheresatOptions(branch=CHILD),
        _ESTABLISHED,
        where="checkout",
    ),
    "branch-absent": Vector(
        "branch-absent",
        wheresat.WheresatOptions(branch="absent"),
        _UNUSABLE,
        where="checkout",
    ),
    "onto-absent": Vector(
        "onto-absent", wheresat.WheresatOptions(onto="no-such-revision"), _UNUSABLE
    ),
    "parent-named": Vector(
        "parent-named",
        wheresat.WheresatOptions(parent="octocat/hello-world#42"),
        _INDETERMINATE,
    ),
    "parent-malformed": Vector(
        "parent-malformed",
        wheresat.WheresatOptions(parent="hello-world#42"),
        _UNUSABLE,
    ),
    "onto-absent-json": Vector(
        "onto-absent-json",
        wheresat.WheresatOptions(json=True, onto="no-such-revision"),
        _UNUSABLE,
    ),
    "refusal": Vector(
        "refusal",
        wheresat.WheresatOptions(branch=PARENT, onto=PARENT),
        _REFUSED,
        where="checkout",
    ),
    "op-id-escapes": Vector(
        "op-id-escapes", wheresat.WheresatOptions(op_id="../escape"), _UNUSABLE
    ),
    "op-id-dash": Vector(
        "op-id-dash", wheresat.WheresatOptions(op_id="-dash"), _UNUSABLE
    ),
    "op-id-colon": Vector(
        "op-id-colon", wheresat.WheresatOptions(op_id="colon:name"), _UNUSABLE
    ),
    "op-id-newline": Vector(
        "op-id-newline", wheresat.WheresatOptions(op_id="line\nbreak"), _UNUSABLE
    ),
    "op-id-dots": Vector(
        "op-id-dots", wheresat.WheresatOptions(op_id="a..b"), _UNUSABLE
    ),
    "op-id-trailing-dot": Vector(
        "op-id-trailing-dot", wheresat.WheresatOptions(op_id="a."), _UNUSABLE
    ),
    "op-id-lock": Vector(
        "op-id-lock", wheresat.WheresatOptions(op_id="a.lock"), _UNUSABLE
    ),
}

_STATUSES: typ.Final[frozenset[int]] = frozenset({
    _ESTABLISHED,
    _REFUSED,
    _UNUSABLE,
    _INDETERMINATE,
})
"""Every status the command documents, each of which the matrix must reach."""


@pytest.fixture(scope="module")
def scenario(tmp_path_factory: pytest.TempPathFactory) -> WheresatScenario:
    """Return the stacked checkout every vector of the matrix reads.

    One checkout is shared because none of the vectors may change it: the
    fingerprint of each example is compared before and after its own run, so an
    example that disturbed the repository fails where it happened rather than
    leaving a later example to fail for its predecessor's reason.

    Returns
    -------
    WheresatScenario
        The checkout, with the boundary its record attests.

    """
    return stacked_child(tmp_path_factory.mktemp("wheresat-read-only"))


@pytest.fixture
def answerable(tmp_path: Path) -> Journey:
    """Return a journey whose forge answers the parent pull request.

    The matrix's checkout is read without a forge, so its ``--parent`` vector
    can only be reported ``unavailable``: the fetch a named parent drives is
    reachable only when something answers for the pull request. The journey is
    built per test rather than shared, because the head a fetch writes is
    cached, and a warmed cache would answer the run that may not fetch.

    Returns
    -------
    Journey
        The squash-merged background, with a forge that knows its parent.

    """
    return wheresat_scenarios.squashed(tmp_path / "answerable")


@pytest.fixture
def dirtied(tmp_path: Path) -> WheresatScenario:
    """Return a stacked child whose worktree holds an uncommitted change."""
    dirty = stacked_child(tmp_path)
    (dirty.worktree_path() / _TRACKED).write_text("edited in the worktree")
    return dirty


@pytest.fixture
def rebasing(tmp_path: Path) -> WheresatScenario:
    """Return a stacked child whose worktree is stopped inside a rebase."""
    stopped = stacked_child(tmp_path)
    _stop_a_rebase(stopped)
    return stopped


def _run_in(
    scenario: WheresatScenario,
    vector: Vector,
    capsys: pytest.CaptureFixture[str],
) -> WheresatRun:
    """Run ``vector`` where it says it belongs, and return what it reported.

    Parameters
    ----------
    scenario : WheresatScenario
        The checkout the vector is run against.
    vector : Vector
        The command line to run, and the working tree to run it from.
    capsys : pytest.CaptureFixture[str]
        Capture fixture the run's output is read from.

    Returns
    -------
    WheresatRun
        The status and both output streams.

    """
    where = (
        scenario.worktree_path() if vector.where == "worktree" else scenario.local_path
    )
    with in_directory(where):
        return run_wheresat(vector.options, capsys)


def _reading(scenario: WheresatScenario) -> Fingerprint:
    """Return the fingerprint of both working trees of ``scenario``.

    Parameters
    ----------
    scenario : WheresatScenario
        The checkout to take a reading of.

    Returns
    -------
    Fingerprint
        The reading, comparable with :meth:`Fingerprint.differences`.

    """
    return fingerprint(
        scenario.local_path,
        scenario.worktree_path(),
        repo=scenario.repo,
    )


def _observed(
    scenario: WheresatScenario,
    vector: Vector,
    capsys: pytest.CaptureFixture[str],
    recorder: RecordingRecorder,
) -> list[observability.Observation]:
    """Run ``vector`` and return only the observations that run recorded.

    Parameters
    ----------
    scenario : WheresatScenario
        The checkout the vector is run against.
    vector : Vector
        The command line to run, and the working tree to run it from.
    capsys : pytest.CaptureFixture[str]
        Capture fixture the run's output is read from.
    recorder : RecordingRecorder
        The recorder installed for the test.

    Returns
    -------
    list[observability.Observation]
        What the run recorded, in order, with whatever earlier runs in the same
        test recorded left out.

    """
    first = len(recorder.observations)
    _run_in(scenario, vector, capsys)
    return recorder.observations[first:]


def _git_directory(scenario: WheresatScenario, branch: str) -> Path:
    """Return the Git directory the worktree holding ``branch`` resolves to.

    Parameters
    ----------
    scenario : WheresatScenario
        The checkout whose worktree is asked about.
    branch : str
        Branch the worktree holds.

    Returns
    -------
    Path
        The directory Git reports for that worktree, which is where Git keeps
        the state of an operation in progress there.

    """
    return Path(scenario.worktree_repo(branch).git.rev_parse("--absolute-git-dir"))


@pytest.mark.parametrize("label", tuple(_VECTORS))
def test_every_vector_leaves_the_repository_alone(
    label: str,
    scenario: WheresatScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Each vector answers what it must and changes nothing.

    The status is checked before the fingerprint, so a vector that refused for
    an unexpected reason cannot be mistaken for one that answered, and the refs
    are then compared strictly — a stronger claim than INV-1 permits, because
    the boundary this fixture's record attests is a commit the parent branch
    already reaches, so no run needs to retain anything.
    """
    vector = _VECTORS[label]
    before = _reading(scenario)
    run = _run_in(scenario, vector, capsys)
    after = _reading(scenario)

    assert run.exit_code == vector.exit_code, (
        f"expected {label} to exit {vector.exit_code}, not {run.exit_code}: "
        f"{run.stderr.strip()}"
    )
    assert before.refs == after.refs, f"expected {label} to write no ref at all"
    assert not before.differences(after), (
        f"expected {label} to leave the repository unchanged"
    )


def test_the_matrix_reaches_every_status() -> None:
    """The matrix exercises each way the command can end.

    A matrix that had quietly stopped reaching a status would still pass every
    per-vector claim while measuring less of the command, so the statuses are
    read off the table itself rather than from what the runs did.
    """
    assert {vector.exit_code for vector in _VECTORS.values()} == set(_STATUSES), (
        "the matrix must reach every status the command can end with"
    )


def test_an_answering_run_reads_the_attested_record(
    scenario: WheresatScenario,
    capsys: pytest.CaptureFixture[str],
    recording_recorder: RecordingRecorder,
) -> None:
    """The established verdict is reached through the record, and says so.

    The boundary in this fixture is attested by the stack record ``git donkey``
    wrote at the child's birth, so the run must both collect evidence at the
    attested tier and reach a verdict on it. Reading that from the observations
    rather than from the printed report is what makes the matrix's evidence
    about the paths it reached rather than about the text it produced.
    """
    observations = _observed(scenario, _VECTORS["default"], capsys, recording_recorder)

    assert any(
        observation.operation == "evidence_collection"
        and observation.evidence_tier == "attested"
        for observation in observations
    ), f"expected attested evidence to be collected, got {observations}"
    assert any(
        observation.operation == "boundary_assessment"
        and observation.verdict == "established"
        for observation in observations
    ), f"expected an established verdict, got {observations}"


def test_a_named_parent_is_asked_for_and_reported_unavailable(
    scenario: WheresatScenario,
    capsys: pytest.CaptureFixture[str],
    recording_recorder: RecordingRecorder,
) -> None:
    """A parent the run may not consult is reported, not silently ignored.

    ``--parent`` names a pull request the local evidence path cannot reach, so
    the run records that it asked and could not answer, and returns the
    indeterminate verdict that says so rather than a verdict about evidence it
    never saw.
    """
    observations = _observed(
        scenario, _VECTORS["parent-named"], capsys, recording_recorder
    )

    assert any(
        observation.operation == "parent_identification"
        and observation.outcome == "unavailable"
        for observation in observations
    ), f"expected the parent to be asked for, got {observations}"
    assert any(
        observation.operation == "boundary_assessment"
        and observation.verdict == "indeterminate"
        for observation in observations
    ), f"expected an indeterminate verdict, got {observations}"


def test_a_run_that_may_fetch_fetches_the_head_and_writes_only_evidence(
    answerable: Journey,
    capsys: pytest.CaptureFixture[str],
    recording_recorder: RecordingRecorder,
) -> None:
    """A parent the forge answers for is fetched, and its head cached as evidence.

    This is the fetch the matrix's ``--no-fetch`` vector suppresses and the run
    beside it performs, which is what makes the flag mean something: the same
    question is asked twice, one flag apart, and only the run that may fetch
    reaches ``success``. The head lands under the evidence namespace, which is
    the one difference INV-1 permits, so the fingerprint is compared to say that
    is the only thing the run wrote.
    """
    options = wheresat.WheresatOptions(parent=wheresat_scenarios.PULL_REQUEST)
    before = wheresat_scenarios.reading_of(answerable)
    first = len(recording_recorder.observations)
    run = answerable.run(options, capsys)
    observations = recording_recorder.observations[first:]
    after = wheresat_scenarios.reading_of(answerable)
    reached = {
        (observation.operation, observation.outcome) for observation in observations
    }
    cache = str(wheresat_refs.parent_head_ref(answerable.identity))

    assert run.exit_code == _ESTABLISHED, (
        f"a fetched parent is established, but the run exited {run.exit_code}: "
        f"{run.stderr.strip()}"
    )
    assert {("parent_identification", "found"), ("evidence_fetch", "success")} <= (
        reached
    ), f"expected the parent to be answered for and fetched, got {observations}"
    assert answerable.scenario.repo.git.rev_parse(cache) == answerable.parent_head, (
        "the fetched head is cached as the head the pull request records"
    )
    assert not before.differences(after), (
        "the evidence ref is the only thing the run wrote"
    )


def test_a_run_that_may_not_fetch_records_the_refusal_and_writes_nothing(
    answerable: Journey,
    capsys: pytest.CaptureFixture[str],
    recording_recorder: RecordingRecorder,
) -> None:
    """``--no-fetch`` is what stops the fetch, and no ref stands in its place.

    The cache holds no head for this journey, so the run that may not fetch has
    nothing to read, records that it did not ask, and answers indeterminate
    rather than established. It is the pair to the test above: one journey, one
    flag, and the two runs differ in the fetch and in what it wrote.
    """
    options = wheresat.WheresatOptions(
        parent=wheresat_scenarios.PULL_REQUEST, no_fetch=True
    )
    before = wheresat_scenarios.reading_of(answerable)
    first = len(recording_recorder.observations)
    run = answerable.run(options, capsys)
    observations = recording_recorder.observations[first:]
    after = wheresat_scenarios.reading_of(answerable)

    assert run.exit_code == _INDETERMINATE, (
        f"an unfetched parent cannot be established, but the run exited "
        f"{run.exit_code}: {run.stderr.strip()}"
    )
    assert any(
        observation.operation == "evidence_fetch"
        and observation.outcome == "not_requested"
        for observation in observations
    ), f"expected the fetch to be reported as not requested, got {observations}"
    assert "--no-fetch" in run.stdout, "and the report names the flag that stopped it"
    assert before.refs == after.refs, "and nothing is written in the head's place"
    assert not before.differences(after), "and the repository is left as it was found"


def test_a_refused_run_never_reaches_an_assessment(
    scenario: WheresatScenario,
    capsys: pytest.CaptureFixture[str],
    recording_recorder: RecordingRecorder,
) -> None:
    """A run that cannot resolve its branch stops before weighing any evidence.

    This is the error-path half of the matrix's non-vacuity: a run that refuses
    its arguments must not spend the run collecting evidence, because evidence
    is not what it is missing.
    """
    observations = _observed(
        scenario, _VECTORS["branch-absent"], capsys, recording_recorder
    )

    assert not any(
        observation.operation in {"evidence_collection", "boundary_assessment"}
        for observation in observations
    ), f"expected the run to stop at its arguments, got {observations}"


def test_the_deep_vector_collects_what_the_default_run_does_not(
    scenario: WheresatScenario,
    capsys: pytest.CaptureFixture[str],
    recording_recorder: RecordingRecorder,
) -> None:
    """``--deep`` reaches the content comparison, and the default run does not.

    The inferred tier is the one only a comparison reaches: evidence read off the
    tree, where the rest of the matrix is evidence read out of a record or left
    unavailable. Both vectors leave the repository alone, so without this the
    invariant would hold just as well over a run that never compared anything.
    """

    def inferred(observations: list[observability.Observation]) -> set[str]:
        """Return the operations the run recorded at the inferred tier."""
        return {
            observation.operation
            for observation in observations
            if observation.evidence_tier == "inferred"
        }

    default = _observed(scenario, _VECTORS["default"], capsys, recording_recorder)
    deep = _observed(scenario, _VECTORS["deep"], capsys, recording_recorder)

    assert not inferred(default), (
        f"the default run asks for no comparison, got {default}"
    )
    assert "evidence_collection" in inferred(deep), (
        f"expected the deep run to compare contents, got {deep}"
    )


def test_the_json_vector_reports_the_boundary_the_record_attests(
    scenario: WheresatScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The machine-readable envelope names the boundary the record attests.

    The envelope is the interface a later milestone's ``--record`` writes from,
    so its established shape is pinned here: the boundary the record attests,
    the child it belongs to, the command that would replay the child's work, and
    no durable ref, because this run retained nothing.
    """
    run = _run_in(scenario, _VECTORS["json"], capsys)
    payload = run.envelope

    assert payload["schema"] == "git-wheresat/1", (
        "the envelope declares the schema a consumer reads it as"
    )
    assert payload["verdict"] == "established", "the record's boundary is established"
    assert payload["exitCode"] == _ESTABLISHED, (
        "and the run exits with the established status"
    )
    assert payload["oldBase"] == scenario.boundary, (
        "naming the boundary the record attests"
    )
    assert payload["child"] == {"branch": CHILD, "tip": scenario.tip}, (
        "and the child it belongs to"
    )
    assert payload["durableRef"] is None, (
        "and no durable ref, because this run retained nothing"
    )
    assert payload["warnings"] == [], "and no warning, because nothing needed repairing"
    assert payload["rebaseCommand"] == (
        f"git rebase --onto {payload['target']} {scenario.boundary} {CHILD}"
    ), "and the replay the boundary implies"


def test_a_refused_run_names_its_gate_and_prints_no_replay_command(
    scenario: WheresatScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A refusal says which gate refused, and offers nothing to run.

    The gate that decides this vector's answer is the replay range being empty:
    the candidate boundary is the child's own tip, so there is no work between
    the two to replay. Nothing may be printed for a replay either — a refusal
    that printed a command would be read as an answer by anyone who ran it.
    """
    run = _run_in(scenario, _VECTORS["refusal"], capsys)
    gate = wheresat_records.GateName.REPLAY_RANGE_NON_EMPTY.value

    assert any(
        line.split()[:2] == ["failed", gate] for line in run.stdout.splitlines()
    ), f"expected {gate} to be reported as failed, got:\n{run.stdout}"
    assert "git rebase --onto" not in run.stdout, "a refusal offers nothing to run"


def test_a_dirty_worktree_is_warned_about_and_left_alone(
    dirtied: WheresatScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Uncommitted changes warn, do not decide, and are still there afterwards.

    The command prints a replay that would refuse to run over these changes, so
    it says so; that is not evidence about the boundary, so it changes neither
    the verdict nor the status.
    """
    before = _reading(dirtied)
    run = _run_in(dirtied, _VECTORS["default"], capsys)

    assert run.exit_code == _ESTABLISHED, (
        "a dirty worktree warns but does not change the verdict"
    )
    assert "has uncommitted changes" in run.stdout, "the warning names the obstacle"
    assert not before.differences(_reading(dirtied)), (
        "and the worktree is left exactly as it was"
    )
    assert (
        dirtied.worktree_path() / _TRACKED
    ).read_text() == "edited in the worktree", (
        "including the uncommitted edit, which is still there"
    )


def test_a_stopped_rebase_is_warned_about_and_left_running(
    rebasing: WheresatScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A stopped rebase warns, does not decide, and is still stopped afterwards.

    This is the case the warning exists for: the child's work cannot be replayed
    where a rebase already is, and a run that tidied the rebase away would have
    destroyed the user's work to answer a question about a commit. The branch is
    therefore named rather than read from the checkout, because a worktree
    stopped this way has a detached ``HEAD``, and the warning about the
    uncommitted conflict is expected alongside the warning about the rebase.
    """
    before = _reading(rebasing)
    run = _run_in(rebasing, _VECTORS["branch-named"], capsys)

    assert run.exit_code == _ESTABLISHED, (
        "a stopped rebase warns but does not change the verdict"
    )
    assert "a rebase is already in progress" in run.stdout, (
        "the warning names the rebase"
    )
    assert "has uncommitted changes" in run.stdout, "and the conflict it stopped on"
    assert not before.differences(_reading(rebasing)), (
        "and the rebase is left running where it was"
    )
    assert _git_directory(rebasing, CHILD).joinpath("rebase-merge").is_dir(), (
        "so the rebase directory is still there"
    )


def _stop_a_rebase(scenario: WheresatScenario) -> None:
    """Leave the child's worktree stopped inside a conflicting rebase.

    The child commits one version of a tracked file and the parent commits a
    different version of the same file, so replaying the child's work over the
    parent cannot apply and Git stops with the rebase in progress. The parent is
    advanced from the main checkout, which is where that branch is checked out:
    the child is checked out in its own worktree, and Git refuses to check a
    branch out twice.

    Parameters
    ----------
    scenario : WheresatScenario
        The checkout whose child worktree is left mid-rebase.

    """
    worktree = scenario.worktree_repo()
    _commit(worktree, scenario.worktree_path(), "child side\n", "Child rewrites it")
    repo = scenario.repo
    previous = repo.head.ref.name
    repo.git.checkout(scenario.parent)
    _commit(repo, scenario.local_path, "parent side\n", "Parent rewrites it")
    repo.git.checkout(previous)
    status, _, stderr = worktree.git.rebase(
        scenario.parent, with_extended_output=True, with_exceptions=False
    )

    assert status != 0, f"expected the rebase to conflict, but it did not: {stderr}"


def _commit(repo: Repo, root: Path, text: str, message: str) -> str:
    """Write ``text`` to the tracked file in ``root`` and commit the change.

    Parameters
    ----------
    repo : git.Repo
        Repository to commit in.
    root : Path
        Working tree holding the file.
    text : str
        Contents to write to the tracked file.
    message : str
        Commit message.

    Returns
    -------
    str
        The ID of the commit that made the change.

    """
    (root / _TRACKED).write_text(text)
    repo.git.add(_TRACKED)
    repo.git.commit("-m", message)
    return repo.head.commit.hexsha
