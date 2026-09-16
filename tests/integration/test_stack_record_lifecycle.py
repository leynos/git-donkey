"""The record lifecycle as a state machine, checked against a real repository.

INV-10 is the one requirement in this work whose correctness depends on the
*history* of operations rather than on a single input: ``git donkey``,
``git plonk``, and ``git wheresat --record`` all write the same artefacts, and
any of them can be interrupted, refused, or preceded by a name that was
deleted yesterday. Enumerating those interleavings by hand would miss exactly
the ones that matter, so the sequences are generated instead.

Every step is checked against Git itself rather than against the machine's
bookkeeping: one ``for-each-ref`` and one ``config --list`` are read into a
snapshot, and the snapshot is reconciled with the store's own pure
reconciliation. Two claims are made after every step.

- INV-10: each branch name is in exactly one of the three states — no record,
  a live record, or a tombstone — and a state that is neither is reported as
  malformed rather than silently read as one of them.
- INV-9: the records whose branch has gone are exactly the orphans the store
  reports. A deletion through plain Git leaves one behind until the sweep runs,
  so the invariant is stated as "the store knows about every violation" rather
  than "there are no violations"; the sweep then asserts the empty set.

Two bundles rather than one hold the branches the machine created: membership
of ``_unrecorded`` means "has no record yet" and of ``_recorded`` means "has
one". Choosing a branch to refresh from the second bundle is what makes the
rule exact — a precondition cannot see the argument a rule was drawn with, so
a single bundle would leave a rule enabled whose argument it must then ignore,
and a refresh would be reported as reached without having run. The run requires
each of a tombstone, a refresh, and an orphan to have been reached for the same
reason: a generator that stopped exercising the lifecycle would otherwise pass
quietly. Those three are reached by the ``start`` rule before the first step is
generated, because the generator does not reach them reliably and cannot be
made to; that rule carries the measurements. The negative control in the last
test shows the exclusivity check rejecting the state a leaky create would leave
behind.

Marked with a longer timeout than the suite default because the generated
sequences run thousands of Git subprocesses.
"""

from __future__ import annotations

import dataclasses
import tempfile
import typing as typ
from pathlib import Path

import pytest
from git import GitCommandError, Repo
from hypothesis import settings
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    consumes,
    initialize,
    precondition,
    rule,
    run_state_machine_as_test,
)

from git_donkey import donkey, observability, stack_records, stack_store
from tests import git_repo_helpers
from tests.integration import donkey_helpers

if typ.TYPE_CHECKING:
    from tests.observability_helpers import RecordingRecorder

pytestmark = pytest.mark.timeout(120)

_TRUNK = "main"
_NAMES: typ.Final = ("parent", "child", "feature/nested")

# A pull-request parent, so the generated records exercise the ``v1:pr:`` form
# that ``git wheresat --record`` writes rather than the branch form that birth
# writes; the two renderers share a parser that must accept both.
_PARENT = stack_records.StackParent(
    branch=None,
    pull_request=stack_records.PullRequestIdentity(
        repository="owner/repository", number=1
    ),
)

_RECORD_SUFFIXES: typ.Final = frozenset(key.value for key in stack_records.RecordKey)

# The evidence kind and the retention expression this module writes. Both are
# the values the commands will pass, spelled here rather than imported because
# the store takes whatever it is handed: the production constant for each lands
# with the command that first has a reason to name it.
_REFRESH_EVIDENCE: typ.Final = "stack-record-refreshed"
_EXPIRE: typ.Final = "90.days.ago"

_unrecorded = Bundle("unrecorded")
_recorded = Bundle("recorded")

# What the run must reach before the invariants it checks count for anything: a
# tombstone, a refresh, and a record outliving its branch. The ``start`` rule
# reaches them before generating a step, so the requirement is met without
# asking the sampler for it — see that rule for why asking does not work.
_REQUIRED: typ.Final = frozenset({"tombstone", "refresh", "orphan"})


@dataclasses.dataclass(slots=True)
class _Branch:
    """A branch the machine created, and what it knows about it.

    A mutable holder rather than a name, because ``git branch -m`` changes the
    name the same branch is known by while the generated sequence still refers
    to it; a bundle of names would hold a stale one from then on.

    Parameters
    ----------
    name : str
        Name the branch currently has.
    base : str
        Exclusive replay boundary the branch was created from.
    tip : str
        Commit the branch currently names.

    """

    name: str
    base: str
    tip: str


@dataclasses.dataclass(frozen=True, slots=True)
class _Snapshot:
    """What the record artefacts said when Git was last read.

    Parameters
    ----------
    branches : frozenset[str]
        Names in ``refs/heads``.
    anchors : dict[str, str]
        Branch to the commit in ``refs/stack-bases``.
    tombstones : dict[str, str]
        Branch to the commit in ``refs/stack-tombstones``.
    sections : dict[str, dict[str, str]]
        Branch to its own configuration section.

    """

    branches: frozenset[str]
    anchors: dict[str, str]
    tombstones: dict[str, str]
    sections: dict[str, dict[str, str]]

    @classmethod
    def read(cls, repo: Repo) -> _Snapshot:
        """Read every artefact the record is split across, in two calls."""
        refs = {}
        namespaces = (
            stack_records.BASE_NAMESPACE,
            stack_records.TOMBSTONE_NAMESPACE,
            "refs/heads",
        )
        for line in repo.git.for_each_ref(
            "--format=%(refname) %(objectname)", *namespaces
        ).splitlines():
            ref, _, value = line.partition(" ")
            refs[ref] = value
        return cls(
            branches=frozenset(_under(refs, "refs/heads")),
            anchors=_under(refs, stack_records.BASE_NAMESPACE),
            tombstones=_under(refs, stack_records.TOMBSTONE_NAMESPACE),
            sections=_sections(repo),
        )


def _under(refs: dict[str, str], namespace: str) -> dict[str, str]:
    """Return the refs under ``namespace``, keyed by the name below it."""
    prefix = f"{namespace}/"
    return {
        ref[len(prefix) :]: value
        for ref, value in refs.items()
        if ref.startswith(prefix)
    }


def _sections(repo: Repo) -> dict[str, dict[str, str]]:
    """Return each branch's own configuration section, read from Git."""
    sections: dict[str, dict[str, str]] = {}
    for entry in repo.git.config("--local", "--list", "-z").split("\0"):
        key, separator, value = entry.partition("\n")
        if not separator or not key.startswith("branch."):
            continue
        branch, _, variable = key[len("branch.") :].rpartition(".")
        if variable in _RECORD_SUFFIXES:
            sections.setdefault(branch, {})[variable] = value
    return sections


def _identified_names(snapshot: _Snapshot) -> set[str]:
    """Return every branch name the artefacts mention, record or anchor."""
    return set(snapshot.sections) | set(snapshot.anchors)


def _assert_exclusive(snapshot: _Snapshot, name: str) -> None:
    """Assert that ``name`` is in exactly one of INV-10's three states.

    The states are: no record, a live record, or a tombstone. A live record
    never stands beside a tombstone for the same name, a record is never used
    for a branch that has gone, and anything half-written is reported as
    malformed rather than read as a record.

    Parameters
    ----------
    snapshot : _Snapshot
        The artefacts, as last read from Git.
    name : str
        Branch name to check.

    """
    exists = name in snapshot.branches
    record = stack_records.reconcile(
        name,
        snapshot.sections.get(name, {}),
        snapshot.anchors.get(name),
        branch_exists=exists,
    )
    tombstone = snapshot.tombstones.get(name)
    match record:
        case stack_records.StackRecord():
            assert exists, f"{name!r} has a record although the branch has gone"
            assert tombstone is None, (
                f"{name!r} has a live record and a tombstone at the same time"
            )
        case stack_records.RecordOrphaned():
            assert not exists, f"{name!r} is reported orphaned although it exists"
        case stack_records.RecordMalformed():
            assert exists, (
                f"{name!r} is reported malformed although the branch has gone, "
                "which is an orphan rather than a half-record"
            )


class _Lifecycle(RuleBasedStateMachine):
    """Sequences of the record lifecycle's operations over up to three branches.

    Each rule is one operation a user or another command performs, and the
    machine keeps only the bookkeeping the operations themselves cannot answer:
    which branch objects exist, and what the store last observed about them.
    Everything else is read back from Git and reconciled after every step.
    """

    # Every operation the run reached, which the test requires classes of.
    reached: typ.ClassVar[set[str]] = set()

    # Assigned by ``__init__`` and refreshed by every ``_settle``, which runs
    # at the end of every rule: between one step's end and the next step's
    # preconditions, nothing has touched the repository.
    _observed: _Snapshot

    def __init__(self, root: Path) -> None:
        """Create the repository the sequence runs in.

        Parameters
        ----------
        root : Path
            Directory to create the repository in.

        """
        super().__init__()
        # Every example needs a repository of its own: the machine is built
        # once per example, and ``Repo.init`` over an existing repository
        # would hand the next one the branches the last one left behind.
        self.repo = git_repo_helpers.seed_repo(
            Path(tempfile.mkdtemp(dir=root, prefix="repo-")), branch=_TRUNK
        )
        self.store = stack_store.GitStackRecordWriter(self.repo)
        self.live: dict[str, _Branch] = {}
        self._observed = _Snapshot.read(self.repo)

    @initialize()
    def start(self) -> None:
        """Reach a tombstone, a refresh, and an orphan, then generate freely.

        The classes the run requires are reached here rather than left to the
        generator, because the generator does not reach them reliably. A step
        draws one of a subset of the enabled rules, and that subset varies from
        run to run: over twenty runs of thirty examples and fifteen steps a
        refresh was reached in fifteen, and the first twelve steps of one run
        were twelve draws of the same no-argument rule. Narrowing the enabled
        rules to force a class fares worse — Hypothesis reports a flaky
        strategy, because a precondition that changes which rules are drawable
        makes a replayed example draw differently, and then a health check for
        filtering too much. So the sequence starts from a fixed state instead,
        reached through the same rules a generated step calls, each of which
        checks the invariants after it exactly as a generated step does.

        The steps, in order: a branch is created, recorded, refreshed, and
        entombed; the tombstoned name is taken by the next branch created,
        which retires the tombstone; that branch is recorded and then deleted
        through plain Git, which orphans the anchor it cannot carry away; the
        sweep clears the orphan, and the tombstones are pruned. What the
        generated steps do after that is unconstrained.
        """
        recorded = self.add_branch()
        self.create_record(recorded)
        self.refresh_record(recorded)
        self.entomb_recorded(recorded)
        successor = self.add_branch()
        self.create_record(successor)
        self.delete_through_git(successor)
        self.sweep()
        self.prune()

    @rule(target=_unrecorded)
    @precondition(lambda self: bool(self._reusable_names()))
    def add_branch(self) -> _Branch:
        """Create a branch stacked on the trunk, before ``git donkey`` records it."""
        boundary = self.repo.head.commit.hexsha
        branch = _Branch(
            name=self._reusable_names()[0],
            base=boundary,
            tip=git_repo_helpers.advance(self.repo),
        )
        self.live[branch.name] = branch
        self.repo.git.branch(branch.name, branch.tip)
        self.reached.add("add_branch")
        self._settle()
        return branch

    @rule(branch=consumes(_unrecorded), target=_recorded)
    def create_record(self, branch: _Branch) -> _Branch:
        """Record the branch, as ``git donkey`` does at its birth."""
        self.store.create(_record(branch))
        self.reached.add("create")
        self._settle()
        return branch

    @rule(branch=_recorded)
    def refresh_record(self, branch: _Branch) -> None:
        """Re-observe the branch's tip, as ``git wheresat --record`` does."""
        branch.tip = git_repo_helpers.commit_on(self.repo, branch.name)
        self.store.refresh(
            _record(branch, evidence=_REFRESH_EVIDENCE),
            expected_old=self._observed_anchor(branch),
        )
        self.reached.add("refresh")
        self._settle()

    @rule(branch=_recorded)
    @precondition(lambda self: bool(self._unused_names()))
    def rename_branch(self, branch: _Branch) -> None:
        """Rename the branch, which carries its record and leaves its anchor."""
        previous = branch.name
        branch.name = self._unused_names()[0]
        del self.live[previous]
        self.live[branch.name] = branch
        self.repo.git.branch("-m", previous, branch.name)
        self.reached.add("rename")
        self._settle()

    @rule(branch=consumes(_recorded))
    def entomb_recorded(self, branch: _Branch) -> None:
        """Preserve the tip as a tombstone, then delete, as ``git plonk`` does."""
        self.reached.add("entomb_recorded")
        self._entomb(branch)

    @rule(branch=consumes(_unrecorded))
    def entomb_unrecorded(self, branch: _Branch) -> None:
        """Entomb a branch that has no record of its own, which is the norm."""
        self.reached.add("entomb_unrecorded")
        self._entomb(branch)

    @rule(branch=consumes(_recorded))
    def delete_through_git(self, branch: _Branch) -> None:
        """Delete the branch as a plain Git user would, record and all.

        ``git branch -D`` destroys the whole ``branch.<name>`` section along
        with the ref, so what survives is the anchor alone: the boundary
        commit, not the tip. The orphan this leaves is real — the sweep has to
        clear it — but there is no recorded tip left for the sweep to convert,
        which is exactly why ``git plonk`` writes its tombstone before it
        deletes rather than after.
        """
        self.repo.git.branch("-D", branch.name)
        del self.live[branch.name]
        self.reached.add("delete")
        self._settle()

    @rule(branch=consumes(_recorded))
    def delete_by_ref_surgery(self, branch: _Branch) -> None:
        """Delete the branch ref alone, leaving the record's values behind.

        This is the deletion that lets the sweep do its salvage: the record is
        still parseable, so the tip it recorded can be preserved as a
        tombstone. Nothing in this package deletes a branch this way, but other
        tools and scripts do, and it is the only route by which a foreign
        deletion leaves anything worth preserving.
        """
        self.repo.git.update_ref("-d", f"refs/heads/{branch.name}")
        del self.live[branch.name]
        self.reached.add("ref-surgery")
        self._settle()

    @rule()
    def sweep(self) -> None:
        """Clear the records whose branch has gone, as ``git plonk`` does.

        Two outcomes are distinguishable and both are checked: an orphan whose
        record still parses has its tip preserved as a tombstone, and one whose
        record was destroyed with the branch — the plain ``git branch -D``
        case — is cleared without a tombstone being invented for it.
        """
        if self.store.sweep(self.store.orphans()):
            self.reached.add("swept-tip")
        assert not self.store.orphans(), "the sweep leaves no orphan behind"
        self.reached.add("sweep")
        self._settle()

    @rule()
    def prune(self) -> None:
        """Prune within the retention window, which must delete nothing."""
        assert not self.store.prune(_EXPIRE), (
            "no tombstone written during this run is past the retention window"
        )
        self.reached.add("prune")
        self._settle()

    def _entomb(self, branch: _Branch) -> None:
        """Preserve ``branch``'s tip, then delete it."""
        self.store.entomb(branch.name, branch.tip)
        self.repo.git.branch("-D", branch.name)
        del self.live[branch.name]
        self.reached.add("tombstone")
        self._settle()

    def _reusable_names(self) -> list[str]:
        """Return names a new branch may take: no live branch, no record.

        A name carrying a record is not reusable, because ``create`` refuses to
        overwrite one: what birth should do with the record of a predecessor
        that happened to share the name is not yet decided, so the generated
        sequences never ask. A name carrying only a tombstone is reusable,
        because creating the branch retires that tombstone, and that is part of
        the lifecycle rather than an accident of it.

        The names with a record are those the last snapshot identified — the
        configuration section, the anchor, or both — which is exactly the set
        for which the reader reports anything other than ``RecordAbsent``.

        Returns
        -------
        list[str]
            The free names, in a fixed order so a sequence is reproducible.

        """
        identified = _identified_names(self._observed)
        return [
            name for name in _NAMES if name not in self.live and name not in identified
        ]

    def _unused_names(self) -> list[str]:
        """Return names a rename may take: reusable, and not tombstoned either.

        A rename carries a live record to a new name. Landing it beside a
        tombstone would put one name in two of INV-10's states at once, and a
        test that generated it would be asking the design to answer a question
        it has not answered: nothing in the lifecycle retires a tombstone when
        a *record* arrives at a name, because only ``create`` retires one and a
        rename does not reach ``create``. A branch created from the trunk is
        unrecorded and takes a tombstoned name legitimately, so this restriction
        applies to the rename target alone.

        Returns
        -------
        list[str]
            The free names a rename may land on.

        """
        tombstones = set(self._observed.tombstones)
        return [name for name in self._reusable_names() if name not in tombstones]

    def _observed_anchor(self, branch: _Branch) -> str:
        """Return the anchor value observed for ``branch``, or ``""`` if none.

        A caller has to state what it observed, and after a rename what it
        observes is an absent anchor under the new name even though the record
        is present. An absent anchor is reported as the empty value, which is
        how a caller states that it observed no anchor at all.

        Returns
        -------
        str
            The anchor's value, or the empty value when there is none.

        """
        try:
            observed = self.repo.git.rev_parse(
                "--verify", "--quiet", stack_records.base_ref_path(branch.name)
            )
        except GitCommandError:
            return ""
        return str(observed).strip()

    def _settle(self) -> None:
        """Check INV-10 and INV-9 after one step of the sequence."""
        self._observed = _Snapshot.read(self.repo)
        for name in _NAMES:
            _assert_exclusive(self._observed, name)
        orphans = set(self.store.orphans())
        if orphans:
            self.reached.add("orphan")
        assert orphans == (
            _identified_names(self._observed) - self._observed.branches
        ), "the records whose branch has gone are exactly the orphans reported"


def _record(
    branch: _Branch, *, evidence: str = stack_records.EVIDENCE_BIRTH
) -> stack_records.StackRecord:
    """Return the record for ``branch`` as the machine last observed it."""
    return stack_records.StackRecord(
        branch=branch.name,
        parent=_PARENT,
        base=branch.base,
        recorded_from=branch.tip,
        evidence=evidence,
    )


def test_the_lifecycle_never_leaves_a_branch_in_two_states(tmp_path: Path) -> None:
    """INV-10 over generated operation sequences, checked after every step."""
    _Lifecycle.reached.clear()
    run_state_machine_as_test(
        lambda: _Lifecycle(tmp_path),
        settings=settings(max_examples=20, stateful_step_count=12),
    )

    assert _Lifecycle.reached >= _REQUIRED, (
        "the run must reach a tombstone, a refresh, and an orphan, or the "
        "checks above would pass vacuously: reached "
        f"{sorted(_Lifecycle.reached)}"
    )


def test_the_exclusivity_check_rejects_a_record_beside_a_tombstone(
    tmp_path: Path,
) -> None:
    """The negative control: a leaky create must be rejected, not tolerated."""
    repo = git_repo_helpers.seed_repo(tmp_path / "repo", branch=_TRUNK)
    tip = repo.head.commit.hexsha
    repo.git.branch("child", tip)
    store = stack_store.GitStackRecordWriter(repo)
    store.create(
        stack_records.StackRecord(
            branch="child",
            parent=_PARENT,
            base=tip,
            recorded_from=tip,
            evidence=stack_records.EVIDENCE_BIRTH,
        )
    )
    # The state a create that forgot to retire the tombstone would leave: the
    # name is live again, and still described by its previous incarnation.
    repo.git.update_ref(
        "--create-reflog", stack_records.tombstone_ref_path("child"), tip
    )

    with pytest.raises(AssertionError, match="live record and a tombstone"):
        _assert_exclusive(_Snapshot.read(repo), "child")


def _refuse_writes(monkeypatch: pytest.MonkeyPatch, failure: Exception) -> None:
    """Make every record write raise ``failure``, as a refusing store would.

    The refusal is injected into the store rather than into a writer this test
    builds, so what runs is the whole workflow: the writer the record is handed
    to is the one the command constructed for itself, and the write is the only
    thing replaced. A writer built here would have to be threaded through the
    context the workflow builds for itself, which is what this test did before,
    and it made the test one of those private classes rather than of the
    command.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Patcher the refusal is installed with.
    failure : Exception
        Error every write is to raise, standing for the four ways a write is
        refused: the conflict the store names as its own class, the broader
        error the store reports for any other refusal, the ``ValueError`` its
        ref-path validation raises, and the ``GitCommandError`` a write it does
        not wrap leaves unwrapped.

    """

    def refuse(
        _writer: stack_store.GitStackRecordWriter,
        _record: stack_records.StackRecord,
    ) -> None:
        """Refuse the record, raising the failure this test configured."""
        raise failure

    monkeypatch.setattr(stack_store.GitStackRecordWriter, "create", refuse)


def _stack_a_parent(repo: Repo) -> None:
    """Commit to ``parent`` so that a branch cut from it is a stacked branch.

    A branch created at the trunk commit is not stacked, however it is named,
    and is not recorded at all (INV-11), so a parent still at the trunk would
    leave this test with no record write to refuse.
    """
    repo.git.branch("parent", _TRUNK)
    repo.git.checkout("parent")
    donkey_helpers.seed_repo(repo, "parent.txt", "parent work")
    repo.git.checkout(_TRUNK)


@dataclasses.dataclass(frozen=True, slots=True)
class _Refusal:
    """One of the four ways a record write is refused, and how it is recorded.

    Parameters
    ----------
    failure : Exception
        Error every write raises, standing for one way a store refuses.
    kind : observability.ErrorKind | None
        The kind the run records the refusal as, or ``None`` where the
        vocabulary has no kind for the failure.

    """

    failure: Exception
    kind: observability.ErrorKind | None


_REFUSALS: typ.Final = (
    _Refusal(
        failure=stack_store.StackRecordConflictError("the branch already has a record"),
        kind="stack_record_conflict",
    ),
    _Refusal(
        failure=stack_store.StackRecordError("the store refused the record"),
        kind=None,
    ),
    _Refusal(
        failure=ValueError("the branch name is not a ref path component"),
        kind="stack_record_malformed",
    ),
    _Refusal(
        failure=GitCommandError("git", 128, b"", b"fatal: unable to write ref"),
        kind="git_command_error",
    ),
)
"""The four refusals, in the order the cases are named."""


@dataclasses.dataclass(frozen=True, slots=True)
class _RefusedBirth:
    """A clone whose record writes are refused, and the refusal that refuses them.

    Parameters
    ----------
    scenario : donkey_helpers.DonkeyScenario
        The clone the command is run in, with its parent branch already stacked.
    refusal : _Refusal
        The failure this case injects, and the kind it is recorded as.

    """

    scenario: donkey_helpers.DonkeyScenario
    refusal: _Refusal


@pytest.fixture
def refused_birth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> _RefusedBirth:
    """Return a clone whose record writes raise the case's failure.

    The refusal is installed into the store rather than into a writer this test
    builds, so what runs is the whole command: the writer the record is handed
    to is the one ``git donkey`` constructed for itself. The case comes from the
    test's own parametrization, so the four refusals stay four named cases
    rather than becoming a loop inside one test.

    Parameters
    ----------
    tmp_path : Path
        Directory the clone and its bare remote are created under.
    monkeypatch : pytest.MonkeyPatch
        Patcher the working directory and the refusal are installed with.
    request : pytest.FixtureRequest
        The parametrized case this fixture was asked for.

    Returns
    -------
    _RefusedBirth
        The clone, with a stacked parent and the refusal installed.

    """
    refusal = typ.cast("_Refusal", request.param)
    scenario = donkey_helpers.new_scenario(tmp_path, monkeypatch, "child")
    _stack_a_parent(scenario.repo)
    _refuse_writes(monkeypatch, refusal.failure)
    return _RefusedBirth(scenario=scenario, refusal=refusal)


@pytest.mark.parametrize(
    "refused_birth",
    _REFUSALS,
    ids=["conflict", "store-error", "value-error", "git-error"],
    indirect=True,
)
def test_a_refused_record_write_is_reported_as_a_failed_record(
    capsys: pytest.CaptureFixture[str],
    recording_recorder: RecordingRecorder,
    refused_birth: _RefusedBirth,
) -> None:
    """A record refused after the branch exists is not reported as a bad birth.

    The write is refused four ways: with the conflict the store names as its own
    class, with the store's base error, with the ``ValueError`` its ref-path
    validation raises, and with the ``GitCommandError`` a write it does not wrap
    leaves unwrapped. All four leave the branch created and the record unwritten,
    so all four are reported as a birth whose record could not be written rather
    than as a worktree that could not be added — and all four are recorded, so
    the write step says how it ended rather than stopping at ``started``.
    """
    scenario = refused_birth.scenario
    failure = refused_birth.refusal.failure
    expected_kind = refused_birth.refusal.kind

    with pytest.raises(SystemExit) as excinfo:
        donkey.run_git_donkey(scenario.branch, "parent", no_pull=True)

    stderr = capsys.readouterr().err
    assert excinfo.value.code == 1, "a record that cannot be written is an error"
    assert "the branch was created but its stack record was not written" in stderr, (
        "the failure names the record write rather than the worktree creation"
    )
    assert str(failure) in stderr, "the store's own message is carried through"
    assert "worktree add failed" not in stderr, (
        "the record write is not reported as a failed worktree"
    )
    assert scenario.branch in scenario.repo.heads, (
        "the message is true: the branch really was created"
    )
    assert recording_recorder.outcomes("stack_record_write") == [
        "started",
        "failure",
    ], "the refused write is recorded as a failure, not left in its started state"
    recorded = recording_recorder.error_kinds("stack_record_write")
    assert recorded == ([] if expected_kind is None else [expected_kind]), (
        f"the refusal is recorded as {expected_kind}, got: {recorded}"
    )
