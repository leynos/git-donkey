"""The read-only Git port ``git wheresat`` collects boundary evidence through.

Nothing in this module can write. It resolves revisions, asks ancestry
questions, lists ranges, compares trees and patches. What the worktree holding
the child branch is in the middle of — which the command warns about, because
the replay command it prints will not run in that state — is read by
:mod:`git_donkey.wheresat_worktrees`, and the failures either read can report
are :mod:`git_donkey.wheresat_errors`. The only writing capability in the
command lives in :mod:`git_donkey.wheresat_refs`, which a run constructs only
when it has to fetch or to record, so the default path holds no object that
could mutate the repository (INV-1).

Two properties are maintained here rather than left to the callers.

- **A question Git could not answer is never handed back as a negative
  answer.** A question whose answer is genuinely *no* returns one of the
  negative values — :data:`Ancestry.NOT_ANCESTOR`, an empty tuple, ``None`` —
  and a question Git could not answer at all raises
  :class:`WheresatGraphError`, while an ancestry question the repository's own
  shape makes unanswerable returns :data:`Ancestry.UNKNOWN`. No gate may read
  either of the last two as a refusal (INV-5), so the run reports that it could
  not tell and exits ``3`` rather than ``1``.
- **A shallow repository cannot answer a history question.** A graft cuts
  Git's traversal short, so ``git merge-base --is-ancestor`` reports *not an
  ancestor* for a commit that is one, and ``git rev-list`` lists a range that
  stops at the graft as though it were complete. The ancestry question
  therefore downgrades its negative answers to :data:`Ancestry.UNKNOWN`, and
  the questions that have no value for "could not tell" refuse instead of
  answering: a boundary read from a partial history would cut the child's work
  in the wrong place, which is worse than reporting that the history needs
  deepening. They raise :class:`ShallowHistoryError` rather than the general
  fault, so the report can say "deepen the clone" instead of "a Git command
  failed" without either answer being read as a refusal.

See ``docs/squash-restack-boundary-recovery.md`` for the procedure these reads
serve, and ``docs/execplans/git-wheresat-sub-command.md`` for the measurements
behind the shallow-history rule.

"""

from __future__ import annotations

import dataclasses
import tempfile
import typing as typ

from git import GitCommandError, Repo

from git_donkey._constants import (
    GIT_ANSWERED_NO,
    GIT_ANSWERED_YES,
    REF_NAME_FORMAT,
    WHERESAT_OPERATION_NAMESPACE,
)
from git_donkey.wheresat_errors import (
    ShallowHistoryError,
    WheresatGraphError,
    failure_line,
)
from git_donkey.wheresat_records import Ancestry, WorktreeState
from git_donkey.wheresat_worktrees import worktree_state

_PER_RUN_NAMESPACE: typ.Final = f"{WHERESAT_OPERATION_NAMESPACE}/"
"""Refs a transported boundary would be fetched into, one namespace per run.

No console run creates one: the fetch this command performs writes the durable
cache ref, so nothing a run does puts a commit under this namespace.
"""

_SHALLOW_REFUSAL: typ.Final = (
    "the repository's history is shallow, so Git's traversal stops at the "
    "graft: deepen the clone and retry"
)
"""Why a history question is refused in a shallow repository."""


class WheresatGraph(typ.Protocol):
    """Read-only Git surface required to collect boundary evidence."""

    def resolve(self, rev: str) -> str:
        """Return the full commit object ID for a revision."""

    def is_ancestor(self, ancestor: str, descendant: str) -> Ancestry:
        """Answer one ancestry question, or report that Git could not."""

    def merge_bases(self, left: str, right: str) -> tuple[str, ...]:
        """Return every best common ancestor of two commits."""

    def fork_point(self, upstream_ref: str, head: str) -> str | None:
        """Return the reflog-derived fork point, or None when unavailable."""

    def ref_name(self, rev: str) -> str | None:
        """Return the full ref path a revision names, or None when it names none."""

    def symbolic_ref(self, name: str) -> str | None:
        """Return what a symbolic ref points at, or None when it is not one."""

    def remote_tracking_ref(self, branch: str) -> str | None:
        """Return the remote-tracking ref a branch names, or None when none does."""

    def history(self, rev: str, *, limit: int | None = None) -> tuple[str, ...]:
        """Return commits reachable from rev, oldest first, newest ``limit`` of them."""

    def commits_in_range(
        self, exclude: str, include: str, *, not_reachable_from: str | None = None
    ) -> tuple[str, ...]:
        """Return commits reachable from include and not from exclude."""

    def tree_of(self, rev: str) -> str:
        """Return the tree object ID of a commit."""

    def cumulative_patch_identifier(self, base: str, tip: str) -> str | None:
        """Return one stable patch identifier for the whole `base..tip` diff."""

    def is_reachable_from_durable_ref(self, commit: str) -> bool:
        """Return whether any ref outside the evidence namespace reaches it."""

    def worktree_state(self, branch: str) -> WorktreeState:
        """Return what the worktree holding ``branch`` is in the middle of."""


@dataclasses.dataclass(frozen=True, slots=True)
class GitWheresatGraph:
    """Git-backed reads of the history a boundary question is asked about.

    Parameters
    ----------
    repo : Repo
        Repository to read. This class never writes to it.

    """

    repo: Repo
    _shallowness: bool | None = dataclasses.field(
        default=None, init=False, repr=False, compare=False
    )

    def resolve(self, rev: str) -> str:
        """Return the full commit object ID for a revision.

        Parameters
        ----------
        rev : str
            Revision to resolve, such as a branch name, a ref, or an
            abbreviated object ID.

        Returns
        -------
        str
            The forty-character object ID of the commit ``rev`` names.

        Raises
        ------
        WheresatGraphError
            If Git cannot resolve ``rev`` to exactly one commit.

        """
        try:
            value = self.repo.git.rev_parse(
                "--verify",
                "--end-of-options",
                f"{rev}^{{commit}}",
            )
        except GitCommandError as exc:
            reported = failure_line(exc.stderr, exc.status)
            msg = f"cannot resolve {rev!r} to a commit: {reported}"
            raise WheresatGraphError(msg) from exc
        return str(value).strip()

    def is_ancestor(self, ancestor: str, descendant: str) -> Ancestry:
        """Answer one ancestry question, or report that Git could not.

        The answer is Git's own, except in a shallow repository, where the
        graft has cut the traversal short: a *no* there may be an artifact of
        the missing history rather than an answer, so it is downgraded to
        :data:`Ancestry.UNKNOWN` and no gate may read it as a refusal. A *yes*
        survives the graft, because Git can only find a path that is really
        there, so it is returned as it stands.

        Parameters
        ----------
        ancestor : str
            Commit the question asks about.
        descendant : str
            Commit the question asks against.

        Returns
        -------
        Ancestry
            Whether the first commit is reachable from the second, or
            ``UNKNOWN`` when Git could not tell.

        Raises
        ------
        WheresatGraphError
            If Git cannot answer, such as when an object is missing.

        """
        status, _, stderr = self.repo.git.merge_base(
            "--is-ancestor",
            "--end-of-options",
            ancestor,
            descendant,
            with_extended_output=True,
            with_exceptions=False,
        )
        if status == GIT_ANSWERED_YES:
            return Ancestry.ANCESTOR
        if status != GIT_ANSWERED_NO:
            question = f"cannot tell whether {ancestor} is an ancestor of {descendant}"
            msg = f"{question}: {failure_line(stderr, status)}"
            raise WheresatGraphError(msg)
        if self._is_shallow():
            return Ancestry.UNKNOWN
        return Ancestry.NOT_ANCESTOR

    def merge_bases(self, left: str, right: str) -> tuple[str, ...]:
        """Return every best common ancestor of two commits.

        A shallow repository is refused rather than answered. The commit Git
        reports there is a common ancestor but not necessarily the *best* one,
        because the traversal that would find an older one stopped at the
        graft, and a boundary read from a partial answer cuts the child's work
        in the wrong place.

        Parameters
        ----------
        left : str
            One commit to compare.
        right : str
            The other commit to compare.

        Returns
        -------
        tuple[str, ...]
            The commits that are the best common ancestors of the two, or an
            empty tuple when their histories are unrelated.

        Raises
        ------
        ShallowHistoryError
            If the repository's history is shallow.
        WheresatGraphError
            If Git cannot answer.

        """
        status, output, stderr = self.repo.git.merge_base(
            "--all",
            "--end-of-options",
            left,
            right,
            with_extended_output=True,
            with_exceptions=False,
        )
        if status not in {GIT_ANSWERED_YES, GIT_ANSWERED_NO}:
            reported = failure_line(stderr, status)
            msg = f"cannot find the merge bases of {left} and {right}: {reported}"
            raise WheresatGraphError(msg)
        if self._is_shallow():
            msg = f"cannot trust a merge base of {left} and {right}: {_SHALLOW_REFUSAL}"
            raise ShallowHistoryError(msg)
        return tuple(str(output).split())

    def fork_point(self, upstream_ref: str, head: str) -> str | None:
        """Return the reflog-derived fork point, or ``None`` when unavailable.

        ``git merge-base --fork-point`` reads the upstream ref's reflog, so it
        answers *nothing* once that reflog has expired or when the ref was
        never moved onto a commit the head was based on. ``None`` is that
        answer and not a failure: the source that asked reports no candidate,
        where a raised fault would report that the run could not tell.

        Parameters
        ----------
        upstream_ref : str
            Ref whose reflog is read, such as ``refs/remotes/origin/main``.
        head : str
            Commit to find the fork point of.

        Returns
        -------
        str | None
            The commit the upstream ref last held before the head diverged
            from it, or ``None`` when the reflog yields no such commit.

        Raises
        ------
        ShallowHistoryError
            If the repository's history is shallow.
        WheresatGraphError
            If Git cannot answer.

        """
        status, output, stderr = self.repo.git.merge_base(
            "--fork-point",
            "--end-of-options",
            upstream_ref,
            head,
            with_extended_output=True,
            with_exceptions=False,
        )
        if status not in {GIT_ANSWERED_YES, GIT_ANSWERED_NO}:
            reported = failure_line(stderr, status)
            msg = f"cannot find the fork point of {head} and {upstream_ref}: {reported}"
            raise WheresatGraphError(msg)
        if self._is_shallow():
            msg = (
                f"cannot trust a fork point of {head} and {upstream_ref}: "
                f"{_SHALLOW_REFUSAL}"
            )
            raise ShallowHistoryError(msg)
        if status == GIT_ANSWERED_NO:
            return None
        return str(output).strip()

    def ref_name(self, rev: str) -> str | None:
        """Return the full ref path a revision names, or ``None`` when it names none.

        The fork-point question is about a *ref*: it reads that ref's reflog,
        and Git refuses the question outright when the revision names no ref at
        all. So the caller asks this first and puts the fork-point question only
        about a name it got back, which is also how a run keeps the ref it read
        its target from beside the object ID it resolved that target to.

        Parameters
        ----------
        rev : str
            Revision to look up, such as a branch name or a full ref path.

        Returns
        -------
        str | None
            The full ref path ``rev`` names, or ``None`` when ``rev`` names no
            ref — an object ID has no ref name, and neither has a revision Git
            cannot resolve. Naming no ref is an answer here and not a fault:
            the rev has already been resolved to a commit by the time a caller
            asks, and the only thing in question is whether a reflog exists to
            read.

        Raises
        ------
        WheresatGraphError
            If Git cannot put the question.

        """
        status, output, stderr = self.repo.git.rev_parse(
            "--verify",
            "--quiet",
            "--symbolic-full-name",
            "--end-of-options",
            rev,
            with_extended_output=True,
            with_exceptions=False,
        )
        if status == GIT_ANSWERED_NO:
            return None
        if status != GIT_ANSWERED_YES:
            reported = failure_line(stderr, status)
            msg = f"cannot tell whether {rev!r} names a ref: {reported}"
            raise WheresatGraphError(msg)
        return str(output).strip() or None

    def symbolic_ref(self, name: str) -> str | None:
        """Return what a symbolic ref points at, or ``None`` when it is not one.

        This is how a run reads the remote-tracking default without asking the
        remote: ``refs/remotes/<remote>/HEAD`` is a symbolic ref a clone leaves
        behind, and the branch name inside it is the name the remote advertised
        the last time it was asked.

        Parameters
        ----------
        name : str
            Ref path to read, such as ``refs/remotes/origin/HEAD``.

        Returns
        -------
        str | None
            The ref path ``name`` points at, or ``None`` when ``name`` is not a
            symbolic ref — an ordinary ref, or no ref at all.

        Raises
        ------
        WheresatGraphError
            If Git cannot put the question.

        """
        status, output, stderr = self.repo.git.symbolic_ref(
            "--quiet",
            "--end-of-options",
            name,
            with_extended_output=True,
            with_exceptions=False,
        )
        if status == GIT_ANSWERED_NO:
            return None
        if status != GIT_ANSWERED_YES:
            reported = failure_line(stderr, status)
            msg = f"cannot read what {name} points at: {reported}"
            raise WheresatGraphError(msg)
        return str(output).strip() or None

    def remote_tracking_ref(self, branch: str) -> str | None:
        """Return the remote-tracking ref a branch names, or ``None`` when none does.

        A branch reaches the remote's copy of itself through a ref the last
        fetch left behind, and that ref's name is not the branch's: the branch
        is ``refs/heads/<branch>`` while the ref that tracks its remote is
        ``refs/remotes/<remote>/<branch>``, and a caller that guessed the
        remote could name the wrong one. So every name the branch could be
        known by is tried and the first that resolves to a commit is the
        answer: the name itself when it is already a full ref path, because a
        record that kept one names its own ref; ``refs/remotes/<branch>``, the
        short form a record may have written; and
        ``refs/remotes/<remote>/<branch>`` for each remote the repository
        configures, in Git's own configuration order.

        Parameters
        ----------
        branch : str
            Branch name, or the full ref path a record wrote in its place.

        Returns
        -------
        str | None
            The first candidate that resolves to a commit, as the ref *name*
            and never the object ID it names: a caller that wants the commit
            resolves the name itself, and the name is what says where the head
            was read from. ``None`` says no candidate names a commit, which is
            an answer and not a fault — a branch nobody ever fetched has no
            remote-tracking ref, and that is a fact about the repository rather
            than a question Git could not put.

        Raises
        ------
        WheresatGraphError
            If Git cannot put the question about one of the candidates.

        """
        for candidate in self._remote_tracking_candidates(branch):
            status, _, stderr = self.repo.git.rev_parse(
                "--verify",
                "--quiet",
                "--end-of-options",
                f"{candidate}^{{commit}}",
                with_extended_output=True,
                with_exceptions=False,
            )
            if status == GIT_ANSWERED_YES:
                return candidate
            if status != GIT_ANSWERED_NO:
                reported = failure_line(stderr, status)
                msg = f"cannot tell whether {candidate!r} is a ref: {reported}"
                raise WheresatGraphError(msg)
        return None

    def history(self, rev: str, *, limit: int | None = None) -> tuple[str, ...]:
        """Return the commits reachable from ``rev``, oldest first.

        This is the listing a range cannot express: ``X..X`` is empty by
        construction, so the child's own history — every commit a boundary has
        to partition — has to be asked for as a history rather than as a range.
        ``limit`` keeps the *newest* commits and not the oldest, because a
        window over a long trunk is measured backwards from the tip: the
        commits near the tip are the ones a squash could have landed.

        Parameters
        ----------
        rev : str
            Commit whose history is listed.
        limit : int | None
            How many of the newest commits to keep, or ``None`` for all of
            them.

        Returns
        -------
        tuple[str, ...]
            Commits reachable from ``rev``, oldest first.

        Raises
        ------
        ShallowHistoryError
            If the repository's history is shallow, where a listing stopped at
            the graft would read as a whole history.
        WheresatGraphError
            If Git cannot list the history.

        """
        arguments = [f"--max-count={limit}"] if limit is not None else []
        status, output, stderr = self.repo.git.rev_list(
            "--reverse",
            *arguments,
            "--end-of-options",
            rev,
            with_extended_output=True,
            with_exceptions=False,
        )
        if status != GIT_ANSWERED_YES:
            reported = failure_line(stderr, status)
            msg = f"cannot list the history of {rev}: {reported}"
            raise WheresatGraphError(msg)
        if self._is_shallow():
            msg = f"cannot trust the history of {rev}: {_SHALLOW_REFUSAL}"
            raise ShallowHistoryError(msg)
        return tuple(str(output).split())

    def commits_in_range(
        self,
        exclude: str,
        include: str,
        *,
        not_reachable_from: str | None = None,
    ) -> tuple[str, ...]:
        """Return commits reachable from include and not from exclude.

        ``not_reachable_from`` subtracts a third commit's history from the
        listing, which is how a gate asks what a range still holds beside the
        parent it was integrated with. It is passed as a ``^`` revision rather
        than as ``--not`` so that ``--end-of-options`` stays ahead of the
        revisions and no ref name can be read as an option. The listing is
        reversed because a replay range is read in the order it would be
        replayed, and Git's default order is the reverse of that.

        Parameters
        ----------
        exclude : str
            Commit whose history is left out of the listing.
        include : str
            Commit whose history is listed.
        not_reachable_from : str | None
            Commit whose history is left out as well, when one is named.

        Returns
        -------
        tuple[str, ...]
            Commits reachable from ``include`` and from neither excluded
            commit, oldest first as Git reports them, and so every commit above
            a boundary that partitions the range.

        Raises
        ------
        ShallowHistoryError
            If the repository's history is shallow, where a truncated listing
            would read as a whole one.
        WheresatGraphError
            If Git cannot list the range.

        """
        arguments = [f"^{not_reachable_from}"] if not_reachable_from else []
        status, output, stderr = self.repo.git.rev_list(
            "--reverse",
            "--end-of-options",
            f"{exclude}..{include}",
            *arguments,
            with_extended_output=True,
            with_exceptions=False,
        )
        if status != GIT_ANSWERED_YES:
            reported = failure_line(stderr, status)
            msg = f"cannot list the commits {exclude}..{include}: {reported}"
            raise WheresatGraphError(msg)
        if self._is_shallow():
            msg = f"cannot trust the range {exclude}..{include}: {_SHALLOW_REFUSAL}"
            raise ShallowHistoryError(msg)
        return tuple(str(output).split())

    def tree_of(self, rev: str) -> str:
        """Return the tree object ID of a commit.

        Parameters
        ----------
        rev : str
            Revision whose tree is wanted.

        Returns
        -------
        str
            The forty-character object ID of the commit's tree.

        Raises
        ------
        WheresatGraphError
            If Git cannot resolve ``rev`` to a tree.

        """
        try:
            value = self.repo.git.rev_parse(
                "--verify",
                "--end-of-options",
                f"{rev}^{{tree}}",
            )
        except GitCommandError as exc:
            reported = failure_line(exc.stderr, exc.status)
            msg = f"cannot read the tree of {rev!r}: {reported}"
            raise WheresatGraphError(msg) from exc
        return str(value).strip()

    def cumulative_patch_identifier(self, base: str, tip: str) -> str | None:
        """Return one stable patch identifier for the whole ``base..tip`` diff.

        The comparison that matters for a squash is cumulative: a squash commit
        is an N-to-1 relationship whose diff equals the combined diff of every
        commit above the boundary, so one identifier is computed for the range
        and never one per commit. ``--no-ext-diff`` and ``--no-color`` keep the
        diff in the plain form ``git patch-id`` reads: a configured external
        diff prints a formatted view, and colour for a pipe gets escape
        sequences among the hunks. Either leaves the pipeline with nothing.

        Parameters
        ----------
        base : str
            Commit the range excludes.
        tip : str
            Commit the range ends at.

        Returns
        -------
        str | None
            The identifier Git reports for the range's cumulative diff, or
            ``None`` when the range carries no patch to identify: no diff at
            all, or a change such as a file's mode alone that yields no hunks.

        Raises
        ------
        WheresatGraphError
            If Git cannot diff the range or cannot identify the patch.

        """
        patch = self._range_diff(base, tip)
        if not patch.strip():
            return None
        return self._patch_identifier(patch, base, tip)

    def is_reachable_from_durable_ref(self, commit: str) -> bool:
        """Return whether any ref outside the evidence namespace reaches it.

        A commit named only by the per-run evidence refs — the namespace a
        transported boundary would be fetched into — is one a
        ``git gc --prune=now`` would take with them, so a boundary read from
        one is retained under its own ref before it is reported (INV-8). Every
        other ref counts as durable: branches, tags, remote-tracking refs, the
        stack record's anchors and tombstones, and the boundary refs a previous
        run retained.

        Parameters
        ----------
        commit : str
            Commit to look for.

        Returns
        -------
        bool
            Whether a ref that outlives the process reaches the commit.

        Raises
        ------
        WheresatGraphError
            If Git cannot enumerate the refs that reach ``commit``.

        """
        status, output, stderr = self.repo.git.for_each_ref(
            f"--contains={commit}",
            REF_NAME_FORMAT,
            with_extended_output=True,
            with_exceptions=False,
        )
        if status != GIT_ANSWERED_YES:
            reported = failure_line(stderr, status)
            msg = f"cannot tell whether {commit} is retained by a ref: {reported}"
            raise WheresatGraphError(msg)
        return any(
            not name.startswith(_PER_RUN_NAMESPACE)
            for name in str(output).splitlines()
            if name
        )

    def worktree_state(self, branch: str) -> WorktreeState:
        """Return what the worktree holding ``branch`` is in the middle of.

        The read itself is :func:`git_donkey.wheresat_worktrees.worktree_state`,
        which is part of the port rather than a convenience beside it: a reader
        that could answer every history question and not this one would leave
        the caller that warns about a replay unable to say whether the replay
        can run.

        Parameters
        ----------
        branch : str
            Branch to look for. A branch no worktree has checked out reports an
            idle state rather than a fault, because there is then no working
            tree for a replay to be run in.

        Returns
        -------
        WorktreeState
            The operation the worktree is stopped in, if any, and whether it
            holds changes to tracked files.

        Raises
        ------
        WheresatGraphError
            If Git cannot list the worktrees, open the one holding the branch,
            or read its state.

        """
        return worktree_state(self.repo, branch)

    def _remote_tracking_candidates(self, branch: str) -> tuple[str, ...]:
        """Return the ref names ``branch`` could be known by, in the order tried.

        Parameters
        ----------
        branch : str
            Branch name, or the full ref path a record wrote in its place.

        Returns
        -------
        tuple[str, ...]
            One candidate per spelling, most specific first: ``branch`` itself
            when it is already a full ref path, the short
            ``refs/remotes/<branch>`` form, then one candidate per configured
            remote in the order the repository configures them.

        """
        candidates = [branch] if branch.startswith("refs/") else []
        candidates.append(f"refs/remotes/{branch}")
        candidates.extend(
            f"refs/remotes/{remote.name}/{branch}" for remote in self.repo.remotes
        )
        return tuple(candidates)

    def _is_shallow(self) -> bool:
        """Return whether a graft cuts Git's traversal short.

        The first answer is remembered: five questions ask it, and nothing here writes.

        Returns
        -------
        bool
            Whether a graft cuts Git's traversal short.

        Raises
        ------
        WheresatGraphError
            If Git cannot say whether the history is shallow.

        """
        if self._shallowness is not None:
            return self._shallowness
        try:
            answer = self.repo.git.rev_parse("--is-shallow-repository")
        except GitCommandError as exc:
            reported = failure_line(exc.stderr, exc.status)
            msg = f"cannot tell whether the history is shallow: {reported}"
            raise WheresatGraphError(msg) from exc
        shallowness = str(answer).strip() == "true"
        object.__setattr__(self, "_shallowness", shallowness)
        return shallowness

    def _range_diff(self, base: str, tip: str) -> str:
        """Return the patch that turns ``base``'s tree into ``tip``'s."""
        status, output, stderr = self.repo.git.diff(
            "--no-ext-diff",
            "--no-color",
            "--full-index",
            "--end-of-options",
            base,
            tip,
            with_extended_output=True,
            with_exceptions=False,
        )
        if status != GIT_ANSWERED_YES:
            reported = failure_line(stderr, status)
            msg = f"cannot diff {base} against {tip}: {reported}"
            raise WheresatGraphError(msg)
        return str(output)

    def _patch_identifier(self, patch: str, base: str, tip: str) -> str | None:
        """Return the identifier ``git patch-id`` reports for ``patch``."""
        # The patch is staged in an unnamed temporary file because patch-id
        # reads it from standard input, and GitPython hands that input to the
        # process as an open file rather than as a buffer it writes itself.
        with tempfile.TemporaryFile() as staged:
            staged.write(patch.encode())
            staged.seek(0)
            status, output, stderr = self.repo.git.patch_id(
                "--stable",
                istream=staged,
                with_extended_output=True,
                with_exceptions=False,
            )
        if status != GIT_ANSWERED_YES:
            reported = failure_line(stderr, status)
            msg = f"cannot identify the patch {base}..{tip} introduces: {reported}"
            raise WheresatGraphError(msg)
        fields = str(output).split()
        return fields[0] if fields else None
