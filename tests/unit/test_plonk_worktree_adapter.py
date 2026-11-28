"""Unit tests for ``git-plonk``'s worktree adapter.

The adapter is the seam between the cleanup workflow and Git itself, so these
tests pin both halves of that contract against a real repository: the
cleanliness preflight that mirrors ``git worktree remove``, and the unforced
removal it issues. The two are asserted together, because a preflight that
disagreed with Git in either direction would either destroy uncommitted work or
skip a worktree Git would happily discard. Deleting a branch has no Git
behaviour to mirror, so its success and its refusal are asserted directly. Each
of those three boundaries is asserted to emit its own timed span, refusals
included, because the subprocess that reports the refusal ran too.
"""

from __future__ import annotations

import shutil
import typing as typ
from pathlib import Path
from types import SimpleNamespace

import pytest
from git import GitCommandError, Repo

from git_donkey import plonk, plonk_adapters, plonk_records
from tests import git_repo_helpers

if typ.TYPE_CHECKING:
    from tests.observability_helpers import RecordingRecorder

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
        pytest.param(
            "modified", plonk_records._SkipReason.DIRTY, id="modified-tracked-file"
        ),
        pytest.param("staged", plonk_records._SkipReason.DIRTY, id="staged-change"),
        pytest.param("untracked", plonk_records._SkipReason.DIRTY, id="untracked-file"),
        pytest.param("ignored", None, id="ignored-build-output"),
    ],
)
def test_cleanliness_preflight_matches_git_removal(
    tmp_path: Path,
    dirt: str,
    expected_reason: plonk_records._SkipReason | None,
    recording_recorder: RecordingRecorder,
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
    assert recording_recorder.span_operations() == [
        "worktree_preflight",
        "worktree_removal",
    ], "both Git boundaries the adapter crossed are timed, dirt or not"


def test_skip_reason_reports_a_missing_worktree_directory(tmp_path: Path) -> None:
    """A vanished worktree should be reported as unavailable, not as dirty."""
    repo, worktree_path = _worktree_with_dirt(tmp_path, "clean")
    shutil.rmtree(worktree_path)

    reason = plonk._GitWorktreeAdapter(repo).skip_reason(worktree_path)

    assert reason is plonk_records._SkipReason.UNAVAILABLE, (
        "a worktree whose directory is gone is unavailable, not dirty"
    )


def test_preflight_skips_a_worktree_whose_commit_changed(tmp_path: Path) -> None:
    """A commit made after selection invalidates the completion evidence."""
    repo, worktree_path = _worktree_with_dirt(tmp_path, "clean")
    recorded_commit = repo.commit(_WORKTREE_BRANCH).hexsha
    worktree_repo = Repo(worktree_path)
    (worktree_path / "later.txt").write_text("later")
    worktree_repo.index.add(["later.txt"])
    worktree_repo.index.commit("Later work")

    reason = plonk._GitWorktreeAdapter(repo).skip_reason(
        worktree_path,
        expected_branch=_WORKTREE_BRANCH,
        expected_commit=recorded_commit,
    )

    assert reason is plonk_records._SkipReason.CHANGED, (
        "expected a worktree that advanced past its recorded commit to be kept"
    )


def test_preflight_accepts_a_worktree_matching_its_recorded_identity(
    tmp_path: Path,
) -> None:
    """A worktree still on its recorded branch and commit stays removable."""
    repo, worktree_path = _worktree_with_dirt(tmp_path, "clean")

    reason = plonk._GitWorktreeAdapter(repo).skip_reason(
        worktree_path,
        expected_branch=_WORKTREE_BRANCH,
        expected_commit=repo.commit(_WORKTREE_BRANCH).hexsha,
    )

    assert reason is None, "expected the unchanged worktree to remain removable"


def test_preflight_skips_a_worktree_on_another_branch(tmp_path: Path) -> None:
    """A worktree checked out on a different branch is not the recorded one."""
    repo, worktree_path = _worktree_with_dirt(tmp_path, "clean")

    reason = plonk._GitWorktreeAdapter(repo).skip_reason(
        worktree_path,
        expected_branch="issue-999-other",
        expected_commit=repo.commit(_WORKTREE_BRANCH).hexsha,
    )

    assert reason is plonk_records._SkipReason.CHANGED, (
        "expected a different checked-out branch to be kept"
    )


def test_conditional_deletion_refuses_a_moved_branch(tmp_path: Path) -> None:
    """A branch that advanced past the recorded commit keeps its reference."""
    repo, worktree_path = _worktree_with_dirt(tmp_path, "clean")
    recorded_commit = repo.commit(_WORKTREE_BRANCH).hexsha
    repo.git.worktree("remove", worktree_path.as_posix())
    # Advance the branch from the main checkout, as a concurrent agent would.
    repo.git.checkout(_WORKTREE_BRANCH)
    (Path(repo.working_tree_dir or ".") / "later.txt").write_text("later")
    repo.index.add(["later.txt"])
    repo.index.commit("Later work")
    repo.git.checkout("main")

    deleted = plonk._GitWorktreeAdapter(repo).delete_branch(
        _WORKTREE_BRANCH, expected_commit=recorded_commit
    )

    assert deleted is False, (
        "expected the conditional deletion to refuse a branch that moved"
    )
    assert _WORKTREE_BRANCH in repo.heads, (
        "the branch and its later commit must survive the refusal"
    )


def test_conditional_deletion_keeps_a_branch_in_use_elsewhere(
    tmp_path: Path,
) -> None:
    """A branch another worktree checks out must survive the conditional delete."""
    repo, worktree_path = _worktree_with_dirt(tmp_path, "clean")
    recorded_commit = repo.commit(_WORKTREE_BRANCH).hexsha
    second_worktree = tmp_path / "repo.worktrees" / "second"
    repo.git.worktree(
        "add",
        "-q",
        "--force",  # the branch is already checked out in the first worktree
        second_worktree.as_posix(),
        _WORKTREE_BRANCH,
    )
    assert plonk._GitWorktreeAdapter(repo).remove_worktree(worktree_path) is True, (
        "the completed worktree is removed before the branch is considered"
    )

    deleted = plonk._GitWorktreeAdapter(repo).delete_branch(
        _WORKTREE_BRANCH, expected_commit=recorded_commit
    )

    assert deleted is False, (
        "expected the branch still checked out elsewhere to be kept"
    )
    assert _WORKTREE_BRANCH in repo.heads, (
        "the other worktree must not be left on a missing branch"
    )


def test_conditional_deletion_keeps_a_branch_used_by_a_detached_worktree(
    tmp_path: Path,
) -> None:
    """A detached worktree may be rebasing or bisecting, so the branch stays."""
    repo, worktree_path = _worktree_with_dirt(tmp_path, "clean")
    recorded_commit = repo.commit(_WORKTREE_BRANCH).hexsha
    assert plonk._GitWorktreeAdapter(repo).remove_worktree(worktree_path) is True, (
        "the completed worktree is removed before the branch is considered"
    )
    detached_path = tmp_path / "repo.worktrees" / "detached"
    repo.git.worktree(
        "add", "-q", "--detach", detached_path.as_posix(), recorded_commit
    )

    deleted = plonk._GitWorktreeAdapter(repo).delete_branch(
        _WORKTREE_BRANCH, expected_commit=recorded_commit
    )

    assert deleted is False, "expected a detached worktree to keep the branch in place"
    assert _WORKTREE_BRANCH in repo.heads, "the branch must survive the refusal"


def test_preflight_reports_an_unborn_branch_as_changed(tmp_path: Path) -> None:
    """An unborn HEAD has no commit, so it cannot match a recorded one."""
    repo, _worktree_path = _worktree_with_dirt(tmp_path, "clean")
    recorded_commit = repo.commit(_WORKTREE_BRANCH).hexsha
    unborn_path = tmp_path / "repo.worktrees" / "unborn"
    repo.git.worktree(
        "add", "-q", "--orphan", "-b", "unborn-branch", unborn_path.as_posix()
    )

    reason = plonk._GitWorktreeAdapter(repo).skip_reason(
        unborn_path,
        expected_branch="unborn-branch",
        expected_commit=recorded_commit,
    )

    assert reason is plonk_records._SkipReason.CHANGED, (
        "expected an unborn branch to be reported as changed, not to raise"
    )


def test_conditional_deletion_removes_the_stale_tracking_config(
    tmp_path: Path,
) -> None:
    """The conditional delete must not leave the deleted branch's config behind."""
    repo, worktree_path = _worktree_with_dirt(tmp_path, "clean")
    recorded_commit = repo.commit(_WORKTREE_BRANCH).hexsha
    repo.git.config(f"branch.{_WORKTREE_BRANCH}.remote", "origin")
    repo.git.config(
        f"branch.{_WORKTREE_BRANCH}.merge", f"refs/heads/{_WORKTREE_BRANCH}"
    )
    assert plonk._GitWorktreeAdapter(repo).remove_worktree(worktree_path) is True, (
        "the completed worktree is removed before its branch is deleted"
    )

    deleted = plonk._GitWorktreeAdapter(repo).delete_branch(
        _WORKTREE_BRANCH, expected_commit=recorded_commit
    )

    assert deleted is True, "expected the unchanged branch to be deleted"
    assert _WORKTREE_BRANCH not in repo.heads, "the branch ref is gone"
    # A recreated branch must not inherit the deleted branch's upstream.
    with pytest.raises(GitCommandError):
        repo.git.config("--get", f"branch.{_WORKTREE_BRANCH}.remote")


def test_conditional_deletion_removes_an_unchanged_branch(tmp_path: Path) -> None:
    """Conditional deletion removes the branch while it still matches."""
    repo, worktree_path = _worktree_with_dirt(tmp_path, "clean")
    recorded_commit = repo.commit(_WORKTREE_BRANCH).hexsha
    # The real sweep removes the worktree before it deletes the branch.
    assert plonk._GitWorktreeAdapter(repo).remove_worktree(worktree_path) is True, (
        "the unchanged worktree must be removed before its branch is deleted"
    )

    deleted = plonk._GitWorktreeAdapter(repo).delete_branch(
        _WORKTREE_BRANCH, expected_commit=recorded_commit
    )

    assert deleted is True, "expected the unchanged branch to be deleted"
    assert _WORKTREE_BRANCH not in repo.heads, (
        "expected the deleted branch to be gone from the repository"
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


def test_preflight_ignores_inherited_repository_location(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hook's repository variables must not redirect the preflight."""
    repo, clean_worktree = _worktree_with_dirt(tmp_path, "clean")
    dirty_worktree = tmp_path / "repo.worktrees" / "dirty"
    repo.git.worktree(
        "add",
        "-q",
        dirty_worktree.as_posix(),
        "-b",
        "dirty",
    )
    (dirty_worktree / "README.md").write_text("changed")
    dirty_repo = Repo(dirty_worktree)
    monkeypatch.setenv("GIT_DIR", str(dirty_repo.git_dir))
    monkeypatch.setenv("GIT_WORK_TREE", dirty_worktree.as_posix())

    reason = plonk._GitWorktreeAdapter(repo).skip_reason(clean_worktree)

    assert reason is None, (
        "expected the preflight to inspect the clean candidate, not the "
        "repository location inherited from the invoking hook"
    )


type _UnreadableFailure = typ.Literal[
    "directory", "git-file", "git-command-error", "encoding-error"
]


def _make_preflight_unreadable(
    monkeypatch: pytest.MonkeyPatch,
    failure: _UnreadableFailure,
) -> None:
    """Make one stage of the preflight raise an unreadable-state failure."""
    match failure:
        case "directory":

            def fail_is_dir(self: Path) -> bool:
                """Simulate a path the process is not allowed to stat."""
                raise PermissionError(self)

            monkeypatch.setattr(Path, "is_dir", fail_is_dir)

        case "git-file":

            def fail_repo(path: Path) -> Repo:
                """Simulate a permission error while opening the worktree."""
                raise PermissionError(path)

            monkeypatch.setattr(plonk_adapters, "Repo", fail_repo)

        case "git-command-error" | "encoding-error":
            error: Exception = (
                GitCommandError(["git", "status"], 128)
                if failure == "git-command-error"
                else UnicodeEncodeError("ascii", "é", 0, 1, "ordinal not in range")
            )

            def fail_is_dirty(self: Repo, **kwargs: object) -> bool:
                """Simulate a failure while reading the candidate's status."""
                raise error

            monkeypatch.setattr(Repo, "is_dirty", fail_is_dirty)


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param("directory", id="unreadable-directory"),
        pytest.param("git-file", id="unreadable-git-file"),
        pytest.param("git-command-error", id="unreadable-status"),
        pytest.param("encoding-error", id="encoding-error"),
    ],
)
def test_preflight_reports_unreadable_state_as_a_skip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: _UnreadableFailure,
) -> None:
    """Any unreadable worktree state is a skip, never a batch abort."""
    repo, worktree_path = _worktree_with_dirt(tmp_path, "clean")
    _make_preflight_unreadable(monkeypatch, failure)

    reason = plonk._GitWorktreeAdapter(repo).skip_reason(worktree_path)

    assert reason is plonk_records._SkipReason.UNREADABLE, (
        "expected an unreadable worktree state to be reported as a skip"
    )


def test_history_messages_carry_the_merge_status(tmp_path: Path) -> None:
    """Only a real merge commit may supply classic merge evidence."""
    repo = git_repo_helpers.seed_repo(tmp_path / "repo")
    working_tree = Path(repo.working_tree_dir or ".")
    repo.git.checkout("-b", "feature")
    (working_tree / "feature.txt").write_text("feature")
    repo.index.add(["feature.txt"])
    repo.index.commit("Feature work")
    repo.git.checkout("main")
    repo.git.merge("feature", "--no-ff", "-m", "Merge feature")

    history = list(plonk_adapters._GitWorktreeAdapter(repo).history_messages("HEAD"))
    merge_status = {
        entry.text.splitlines()[0]: entry.is_merge_commit for entry in history
    }

    assert merge_status["Merge feature"] is True, (
        "expected the two-parent commit to be reported as a merge"
    )
    assert merge_status["Feature work"] is False, (
        "expected the one-parent commit to be reported as an ordinary commit"
    )


def test_delete_branch_removes_the_branch_and_times_the_deletion(
    tmp_path: Path,
    recording_recorder: RecordingRecorder,
) -> None:
    """A deletable branch should be gone, with the Git call timed.

    The production adapter is exercised here rather than a Git double, so the
    assertion is about what Git does with the arguments the adapter builds.
    """
    repo = git_repo_helpers.seed_repo(tmp_path / "repo")
    repo.git.branch(_WORKTREE_BRANCH)

    deleted = plonk._GitWorktreeAdapter(repo).delete_branch(_WORKTREE_BRANCH)

    assert deleted is True, "Git deletes a branch that is not checked out here"
    assert _WORKTREE_BRANCH not in {head.name for head in repo.heads}, (
        "the branch is really gone from the repository"
    )
    assert recording_recorder.span_operations() == ["branch_deletion"], (
        "the deletion is timed, whether or not it succeeds"
    )


def test_delete_branch_reports_and_survives_gits_refusal(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    recording_recorder: RecordingRecorder,
) -> None:
    """Git's refusal to delete a checked-out branch should be a reported ``False``.

    A branch cannot be deleted while it is checked out, so this is the refusal a
    real repository produces. The failure is returned rather than raised, the
    branch survives, and the attempt is still timed: the subprocess that
    reported the refusal ran too.
    """
    repo = git_repo_helpers.seed_repo(tmp_path / "repo")
    checked_out = repo.head.reference.name

    deleted = plonk._GitWorktreeAdapter(repo).delete_branch(checked_out)

    assert deleted is False, "the adapter reports the refusal as a boolean"
    assert checked_out in {head.name for head in repo.heads}, (
        "Git's refusal leaves the branch in place"
    )
    assert "failed to delete branch" in capsys.readouterr().err, (
        "the refusal is reported to the user"
    )
    assert recording_recorder.span_operations() == ["branch_deletion"], (
        "the refused deletion is timed as well"
    )
