"""Unit tests for the ``git-plonk`` cleanup workflow.

These tests cover what a sweep does to a completed candidate: the cleanliness
preflight that mirrors ``git worktree remove``, the unforced removal it issues,
the skip-and-report rule that keeps one dirty worktree from abandoning the rest
of the batch, branch deletion in hard mode, dry runs that plan without
mutating, and the soft pass that never resolves a trunk at all.
"""

from __future__ import annotations

import shutil
import typing as typ
from pathlib import Path
from types import SimpleNamespace

import pytest
from git import Repo
from hypothesis import given
from hypothesis import strategies as st

from git_donkey import plonk
from tests import git_repo_helpers

# Trunk ref shape a real run resolves: the fetched remote-tracking ref of the
# default branch the principal remote advertises.
_TRUNK_REF = "refs/remotes/origin/main"

# The branch and directory ``git donkey`` would have created for the candidate
# worktrees these tests exercise.
_WORKTREE_BRANCH = "issue-123-fix"

# Ignore rule the dirt cases rely on: build output must never block cleanup.
_IGNORE_RULE = "build/\n"


def _worktree_with_dirt(tmp_path: Path, dirt: str) -> tuple[Repo, Path]:
    """Create a repository with one linked worktree holding the named dirt.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory that owns the repository and its worktree.
    dirt : str
        State to leave the worktree in: ``clean``, ``modified``, ``staged``,
        ``untracked``, or ``ignored``.

    Returns
    -------
    tuple[Repo, Path]
        The repository and the linked worktree git donkey would have created.

    """
    repo = git_repo_helpers.seed_repo(tmp_path / "repo")
    ignore = Path(repo.working_tree_dir or ".") / ".gitignore"
    ignore.write_text(_IGNORE_RULE)
    repo.index.add([ignore.as_posix()])
    repo.index.commit("Ignore generated output")
    worktree_path = tmp_path / "repo.worktrees" / _WORKTREE_BRANCH
    worktree_path.parent.mkdir()
    repo.git.worktree("add", "-q", worktree_path.as_posix(), "-b", _WORKTREE_BRANCH)
    _apply_dirt(worktree_path, dirt)
    return repo, worktree_path


def _apply_dirt(worktree_path: Path, dirt: str) -> None:
    """Leave ``dirt`` behind in ``worktree_path``.

    Parameters
    ----------
    worktree_path : Path
        Linked worktree to leave the state in.
    dirt : str
        One of ``clean``, ``modified``, ``staged``, ``untracked``, or
        ``ignored``.

    Raises
    ------
    AssertionError
        If ``dirt`` names no supported state, so a typo in a parametrization
        cannot silently pass as a clean worktree.

    """
    readme = worktree_path / "README.md"
    match dirt:
        case "clean":
            pass
        case "modified":
            readme.write_text("changed")
        case "staged":
            readme.write_text("changed")
            Repo(worktree_path).index.add([readme.as_posix()])
        case "untracked":
            (worktree_path / "notes.txt").write_text("scratch")
        case "ignored":
            build = worktree_path / "build"
            build.mkdir()
            (build / "output.bin").write_bytes(b"artifact")
        case _:
            msg = f"unsupported dirt {dirt!r}"
            raise AssertionError(msg)


@pytest.mark.parametrize(
    ("dirt", "expected_reason"),
    [
        pytest.param("clean", None, id="clean"),
        pytest.param("modified", plonk._SkipReason.DIRTY, id="modified-tracked-file"),
        pytest.param("staged", plonk._SkipReason.DIRTY, id="staged-change"),
        pytest.param("untracked", plonk._SkipReason.DIRTY, id="untracked-file"),
        pytest.param("ignored", None, id="ignored-build-output"),
    ],
)
def test_cleanliness_preflight_matches_git_removal(
    tmp_path: Path,
    dirt: str,
    expected_reason: plonk._SkipReason | None,
) -> None:
    """The preflight should clear exactly the worktrees Git removes unprompted.

    The classification and the removal result are asserted together: a
    preflight that disagrees with ``git worktree remove`` in either direction
    would either destroy work or skip a worktree Git would happily discard.
    """
    repo, worktree_path = _worktree_with_dirt(tmp_path, dirt)
    adapter = plonk._GitWorktreeAdapter(repo)

    reason = adapter.skip_reason(worktree_path)
    removed = adapter.remove_worktree(worktree_path)

    assert reason is expected_reason, (
        f"expected the {dirt} worktree to be classified as {expected_reason}"
    )
    assert removed is (expected_reason is None), (
        "Git removes every worktree the preflight clears, and no other"
    )


def test_skip_reason_reports_a_missing_worktree_directory(tmp_path: Path) -> None:
    """A vanished worktree should be reported as unavailable, not as dirty."""
    repo, worktree_path = _worktree_with_dirt(tmp_path, "clean")
    shutil.rmtree(worktree_path)

    reason = plonk._GitWorktreeAdapter(repo).skip_reason(worktree_path)

    assert reason is plonk._SkipReason.UNAVAILABLE, (
        "a worktree whose directory is gone is unavailable, not dirty"
    )


def test_removal_never_forces_git_to_discard_files() -> None:
    """Removal should ask Git politely: no ``--force``, in any mode."""
    calls: list[tuple[str, ...]] = []

    class _RecordingGit:
        """Git surface that records the arguments GitPython passes through."""

        @staticmethod
        def worktree(*arguments: str) -> str:
            """Record one ``git worktree`` invocation.

            Parameters
            ----------
            *arguments : str
                Arguments the adapter passed to ``git worktree``.

            Returns
            -------
            str
                Empty output, as a successful removal produces none.

            """
            calls.append(arguments)
            return ""

    adapter = plonk._GitWorktreeAdapter(
        typ.cast("Repo", SimpleNamespace(git=_RecordingGit()))
    )

    assert adapter.remove_worktree(Path("/repo.worktrees/issue-123-fix")) is True, (
        "the adapter reports Git's success as a boolean"
    )
    assert calls == [("remove", "/repo.worktrees/issue-123-fix")], (
        "the removal is unforced and passes the path as Git expects it"
    )


def test_removal_leaves_a_dirty_worktree_on_disk(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Git's own refusal is the backstop if a dirty candidate reaches removal."""
    repo, worktree_path = _worktree_with_dirt(tmp_path, "modified")
    adapter = plonk._GitWorktreeAdapter(repo)

    removed = adapter.remove_worktree(worktree_path)

    assert removed is False, "Git refuses to discard the uncommitted change"
    assert (worktree_path / "README.md").read_text() == "changed", (
        "the uncommitted change survives the attempt"
    )
    assert "failed to remove worktree" in capsys.readouterr().err, (
        "the refusal is reported to the user"
    )


def test_soft_mode_loads_only_worktree_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Soft mode should avoid trunk ref resolution and global cwd changes."""
    worktrees_root = tmp_path / "repo.worktrees"
    stanzas = [{"worktree": worktrees_root / "issue-123-fix"}]

    monkeypatch.setattr(plonk.helpers, "_find_repo", lambda _prefix: SimpleNamespace())
    monkeypatch.setattr(
        plonk.helpers, "_parse_worktree_porcelain", lambda _repo: stanzas
    )
    monkeypatch.setattr(
        plonk.helpers,
        "_main_worktree_path_from_list",
        lambda _stanzas, _prefix: tmp_path / "repo",
    )
    monkeypatch.setattr(plonk.donkey, "_worktrees_root", lambda _home: worktrees_root)
    monkeypatch.setattr(
        plonk,
        "_load_plonk_context",
        lambda: pytest.fail("soft mode should not load trunk cleanup context"),
    )
    monkeypatch.setattr(
        plonk,
        "_canonical_trunk_ref",
        lambda _repo: pytest.fail("soft mode should not resolve trunk history"),
    )
    monkeypatch.setattr(
        plonk.os,
        "chdir",
        lambda _path: pytest.fail("soft mode should not mutate cwd"),
    )

    exit_code = plonk.run_git_plonk(soft=True)

    assert exit_code == 0, "expected soft git plonk to succeed"
    assert "git-plonk: mode=soft" in capsys.readouterr().out, (
        "expected soft-mode summary output"
    )


def test_dry_run_soft_mode_reports_targets_without_removing_them(
    tmp_path: Path,
) -> None:
    """Dry-run soft mode should inspect generated paths without deleting them."""
    worktrees_root = tmp_path / "repo.worktrees"
    worktree_path = worktrees_root / "issue-123-fix"
    target_path = worktree_path / "target"
    target_path.mkdir(parents=True)

    result = plonk._run_soft(
        [{"worktree": worktree_path}],
        worktrees_root,
        dry_run=True,
    )

    assert result.mode is plonk._PlonkMode.SOFT, "expected soft dry-run mode"
    assert result.is_dry_run, "expected dry-run result marker"
    assert result.cleaned_paths == (target_path,), "expected planned target removal"
    assert target_path.is_dir(), "expected dry run to leave generated path intact"


class _FailingGitAdapter:
    """Git adapter double that plans cleanup but refuses to mutate.

    Dry-run cleanup must reuse candidate discovery while skipping destructive
    Git APIs, so ``remove_worktree`` and ``delete_branch`` fail the test if the
    planner ever invokes them. The completion marker is configurable so callers
    can pin it to the candidate branch under test.
    """

    def __init__(self, marker: str = "Merge pull request (#123)") -> None:
        self._marker = marker

    def history_messages(self, ref: str) -> typ.Iterator[str]:
        """Assert the resolved trunk ref, then yield the completion marker."""
        assert ref == _TRUNK_REF, "expected configured trunk ref"
        yield self._marker

    @staticmethod
    def skip_reason(worktree_path: Path) -> plonk._SkipReason | None:
        """Report every candidate as clean, so planning reaches the mutations."""
        return None

    @staticmethod
    def remove_worktree(worktree_path: Path) -> None:
        """Fail the test unconditionally — dry runs must not remove worktrees."""
        pytest.fail(f"dry run should not remove worktree {worktree_path}")

    @staticmethod
    def delete_branch(branch_name: str) -> None:
        """Fail the test unconditionally — dry runs must not delete branches."""
        pytest.fail(f"dry run should not delete branch {branch_name}")


class _RecordingGitAdapter:
    """Git adapter double that records removals instead of performing them.

    Trunk history is driven by the ``markers`` the double yields and each
    candidate's state by ``skip_reasons``, so one batch can mix worktrees Git
    would remove with worktrees it would refuse. ``removal_failures`` models a
    worktree that passes the preflight but whose removal still fails.
    """

    def __init__(
        self,
        markers: typ.Iterable[str],
        *,
        skip_reasons: dict[Path, plonk._SkipReason] | None = None,
        removal_failures: typ.Iterable[Path] = (),
    ) -> None:
        self._markers = tuple(markers)
        self.skip_reasons = dict(skip_reasons or {})
        self.removal_failures = set(removal_failures)
        self.removed: list[Path] = []
        self.deleted: list[str] = []

    def history_messages(self, ref: str) -> typ.Iterator[str]:
        """Assert the resolved trunk ref, then yield the configured history."""
        assert ref == _TRUNK_REF, "expected configured trunk ref"
        yield from self._markers

    def skip_reason(self, worktree_path: Path) -> plonk._SkipReason | None:
        """Return the configured reason for ``worktree_path``, if it has one."""
        if worktree_path in self.skip_reasons:
            return self.skip_reasons[worktree_path]
        return None

    def remove_worktree(self, worktree_path: Path) -> bool:
        """Record a removal request, reporting success unless told to fail."""
        if worktree_path in self.removal_failures:
            return False
        self.removed.append(worktree_path)
        return True

    def delete_branch(self, branch_name: str) -> None:
        """Record a branch deletion, which must follow its worktree's removal."""
        removed_branches = [path.name for path in self.removed]
        assert branch_name in removed_branches, (
            "hard mode deletes a branch only once its worktree is gone"
        )
        self.deleted.append(branch_name)


def _candidate(branch_name: str, issue_number: int) -> plonk._PlonkCandidate:
    """Return the candidate git donkey creates for ``branch_name``.

    Parameters
    ----------
    branch_name : str
        Branch, and worktree directory name, of the candidate.
    issue_number : int
        Issue number the branch marker refers to.

    Returns
    -------
    plonk._PlonkCandidate
        The candidate, marked complete by the matching issue marker.

    """
    return plonk._PlonkCandidate(
        branch_name=branch_name,
        worktree_path=Path(f"/repo.worktrees/{branch_name}"),
        marker=f"(#{issue_number})",
    )


def _marker_for(candidate: plonk._PlonkCandidate) -> str:
    """Return the trunk history line that marks ``candidate`` complete."""
    return f"Merge pull request {candidate.marker}"


def _context(
    candidates: typ.Iterable[plonk._PlonkCandidate],
) -> plonk._PlonkContext:
    """Return the repository state a completed cleanup of ``candidates`` sees.

    Parameters
    ----------
    candidates : collections.abc.Iterable[plonk._PlonkCandidate]
        Candidates whose worktree stanzas the context reports.

    Returns
    -------
    plonk._PlonkContext
        Context holding one stanza per candidate and no invoking worktree, so
        every candidate is eligible for cleanup.

    """
    return plonk._PlonkContext(
        repo_home=typ.cast("Repo", SimpleNamespace()),
        stanzas=[
            {
                "branch": f"refs/heads/{candidate.branch_name}",
                "worktree": candidate.worktree_path,
            }
            for candidate in candidates
        ],
        worktrees_root=Path("/repo.worktrees"),
        trunk_ref=_TRUNK_REF,
        invoking_worktree=None,
    )


def _cleanup(
    candidates: typ.Iterable[plonk._PlonkCandidate],
    adapter: object,
    *,
    mode: plonk._PlonkMode,
    dry_run: bool = False,
) -> plonk._PlonkResult:
    """Run completed cleanup for ``candidates`` against an adapter double.

    Parameters
    ----------
    candidates : collections.abc.Iterable[plonk._PlonkCandidate]
        Candidates to clean up.
    adapter : object
        Any double exposing the adapter's history, skip, and mutation surface.
    mode : plonk._PlonkMode
        Cleanup mode to run.
    dry_run : bool, optional
        Whether to plan the work without mutating the adapter.

    Returns
    -------
    plonk._PlonkResult
        What the run reports it removed and skipped.

    """
    return plonk._run_completed_cleanup(
        _context(candidates),
        mode,
        typ.cast("plonk._GitWorktreeAdapter", adapter),
        dry_run=dry_run,
    )


def test_dirty_candidate_does_not_abandon_its_clean_siblings() -> None:
    """A skipped worktree should not stop the batch, or the sweep is pointless."""
    dirty = _candidate("issue-456-dirty", 456)
    clean = _candidate("issue-123-clean", 123)
    adapter = _RecordingGitAdapter(
        [_marker_for(dirty), _marker_for(clean)],
        skip_reasons={dirty.worktree_path: plonk._SkipReason.DIRTY},
    )

    result = _cleanup(
        [dirty, clean],
        adapter,
        mode=plonk._PlonkMode.DEFAULT,
    )

    assert result.removed_worktrees == (clean.worktree_path,), (
        "the clean worktree is removed even though its sibling was skipped"
    )
    assert result.skipped_worktrees == (
        plonk._SkippedWorktree(dirty.worktree_path, plonk._SkipReason.DIRTY),
    ), "the dirty worktree is reported with the reason it was left alone"
    assert adapter.removed == [clean.worktree_path], (
        "the dirty worktree is never handed to Git for removal"
    )


def test_hard_mode_keeps_the_branch_of_a_skipped_worktree() -> None:
    """Hard mode deletes completed branches, but only those whose worktree went."""
    clean = _candidate("issue-123-clean", 123)
    dirty = _candidate("issue-456-dirty", 456)
    adapter = _RecordingGitAdapter(
        [_marker_for(clean), _marker_for(dirty)],
        skip_reasons={dirty.worktree_path: plonk._SkipReason.DIRTY},
    )

    result = _cleanup([clean, dirty], adapter, mode=plonk._PlonkMode.HARD)

    assert result.removed_branches == (clean.branch_name,), (
        "only the branch whose worktree was actually removed is deleted"
    )
    assert adapter.deleted == [clean.branch_name], (
        "the skipped worktree keeps the branch that holds its uncommitted work"
    )


def test_dry_run_reports_skips_without_mutating() -> None:
    """A preview should name the worktrees a real run would leave alone."""
    clean = _candidate("issue-123-clean", 123)
    gone = _candidate("issue-456-gone", 456)
    adapter = _RecordingGitAdapter(
        [_marker_for(clean), _marker_for(gone)],
        skip_reasons={gone.worktree_path: plonk._SkipReason.UNAVAILABLE},
    )

    result = _cleanup(
        [clean, gone],
        adapter,
        mode=plonk._PlonkMode.HARD,
        dry_run=True,
    )

    assert result.is_dry_run, "expected dry-run result marker"
    assert result.removed_worktrees == (clean.worktree_path,), (
        "the clean worktree is planned for removal"
    )
    assert result.removed_branches == (clean.branch_name,), (
        "the clean branch is planned for deletion in hard mode"
    )
    assert result.skipped_worktrees == (
        plonk._SkippedWorktree(gone.worktree_path, plonk._SkipReason.UNAVAILABLE),
    ), "the missing worktree is reported as skipped"
    assert not adapter.removed, "a dry run removes no worktree"
    assert not adapter.deleted, "a dry run deletes no branch"


def test_failed_removal_is_skipped_and_the_batch_continues() -> None:
    """A removal Git refuses should report a skip, not abandon the sweep."""
    stubborn = _candidate("issue-456-stubborn", 456)
    clean = _candidate("issue-123-clean", 123)
    adapter = _RecordingGitAdapter(
        [_marker_for(stubborn), _marker_for(clean)],
        removal_failures=[stubborn.worktree_path],
    )

    result = _cleanup([stubborn, clean], adapter, mode=plonk._PlonkMode.HARD)

    assert result.removed_worktrees == (clean.worktree_path,), (
        "the following clean candidate is still removed"
    )
    assert result.removed_branches == (clean.branch_name,), (
        "a branch is only deleted once its worktree is gone"
    )
    assert result.skipped_worktrees == (
        plonk._SkippedWorktree(
            stubborn.worktree_path, plonk._SkipReason.REMOVAL_FAILED
        ),
    ), "the failed removal is reported with its own reason"


@pytest.mark.parametrize(
    ("mode", "expected_branches"),
    [
        (plonk._PlonkMode.DEFAULT, ()),
        (plonk._PlonkMode.HARD, ("issue-123-fix",)),
    ],
)
def test_dry_run_completed_mode_reports_plans_without_mutating(
    mode: plonk._PlonkMode,
    expected_branches: tuple[str, ...],
) -> None:
    """Dry-run completed cleanup should plan work without destructive Git APIs."""
    candidate = _candidate(_WORKTREE_BRANCH, 123)

    result = _cleanup([candidate], _FailingGitAdapter(), mode=mode, dry_run=True)

    assert result.is_dry_run, "expected dry-run result marker"
    assert result.removed_worktrees == (candidate.worktree_path,), (
        "expected planned worktree removal"
    )
    assert result.removed_branches == expected_branches, (
        f"expected planned branch deletion for {mode.value} mode"
    )


@given(
    mode=st.sampled_from((
        plonk._PlonkMode.DEFAULT,
        plonk._PlonkMode.HARD,
    )),
    issue_number=st.integers(min_value=1, max_value=999_999),
)
def test_dry_run_modes_never_mutate_and_report_mode_specific_plans(
    mode: plonk._PlonkMode,
    issue_number: int,
) -> None:
    """Completed-cleanup dry runs should report plans without mutating adapters.

    This pins the pure policy invariant for the ``DEFAULT`` and ``HARD`` modes;
    ``SOFT`` filesystem planning is covered separately by
    ``test_dry_run_soft_mode_reports_targets_without_removing_them``.
    """
    candidate = _candidate(f"issue-{issue_number}-fix", issue_number)

    result = _cleanup(
        [candidate],
        _FailingGitAdapter(_marker_for(candidate)),
        mode=mode,
        dry_run=True,
    )

    expected_branches = (
        (candidate.branch_name,) if mode is plonk._PlonkMode.HARD else ()
    )
    assert result.mode is mode, "expected completed dry-run mode to be preserved"
    assert result.is_dry_run, "expected dry-run result marker"
    assert result.removed_worktrees == (candidate.worktree_path,), (
        "expected planned worktree removal"
    )
    assert result.removed_branches == expected_branches, (
        "expected branch plans only in hard dry-run mode"
    )
