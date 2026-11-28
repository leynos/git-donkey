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
import subprocess  # ruff: ignore[suspicious-subprocess-import]  # asserts timeout mapping
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


def test_upstream_state_uses_the_first_merge_entry(tmp_path: Path) -> None:
    """Git uses the first merge value for ``@{upstream}``, so this check must too."""
    repo, _remote_path, branch = _repository_with_tracking(tmp_path, "present")
    repo.git.config("--add", f"branch.{branch}.merge", "refs/heads/missing")

    state = plonk._GitWorktreeAdapter(repo).upstream_state(branch)

    assert state == "present", (
        "expected the first, still-present merge entry to be authoritative"
    )


def test_upstream_state_reports_gone_when_only_a_later_merge_entry_exists(
    tmp_path: Path,
) -> None:
    """A missing first merge entry is gone even when a later entry exists."""
    repo, _remote_path, branch = _repository_with_tracking(tmp_path, "present")
    repo.git.config(f"branch.{branch}.merge", "refs/heads/missing")
    repo.git.config("--add", f"branch.{branch}.merge", f"refs/heads/{branch}")

    state = plonk._GitWorktreeAdapter(repo).upstream_state(branch)

    assert state == "gone", (
        "expected the first, missing merge entry to decide the upstream state"
    )


def test_upstream_state_uses_the_last_remote_value(tmp_path: Path) -> None:
    """Git uses the last ``branch.<name>.remote`` value for ``@{upstream}``."""
    repo, _origin = git_repo_helpers.repo_with_remote_default(
        tmp_path / "local", tmp_path / "origin.git"
    )
    old_remote = tmp_path / "old.git"
    Repo.init(old_remote, bare=True)
    repo.create_remote("old", old_remote.as_posix())
    branch = _WORKTREE_BRANCH
    repo.git.branch(branch)
    repo.git.push("origin", f"{branch}:refs/heads/{branch}")
    repo.git.config(f"branch.{branch}.remote", "old")
    repo.git.config("--add", f"branch.{branch}.remote", "origin")
    repo.git.config(f"branch.{branch}.merge", f"refs/heads/{branch}")

    state = plonk._GitWorktreeAdapter(repo).upstream_state(branch)

    assert state == "present", (
        "expected the effective origin remote to be probed, not the old one"
    )


def test_upstream_state_preserves_unicode_line_separator(tmp_path: Path) -> None:
    """Git permits U+2028 in refs, so records must split on LF only."""
    repo, _remote_path, branch = _repository_with_tracking(tmp_path, "unset")
    exotic = f"{branch}\u2028next"
    repo.git.branch(exotic)
    repo.git.push("origin", f"{exotic}:refs/heads/{exotic}")
    repo.git.config(f"branch.{branch}.remote", "origin")
    repo.git.config(f"branch.{branch}.merge", f"refs/heads/{exotic}")

    state = plonk._GitWorktreeAdapter(repo).upstream_state(branch)

    assert state == "present", "expected the full ref containing U+2028 to be queried"


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


def test_upstream_state_maps_a_timeout_to_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_recorder: RecordingRecorder,
) -> None:
    """A stalled upstream should become unknown rather than block the sweep."""
    repo, _remote_path, branch = _repository_with_tracking(tmp_path, "present")

    def fail_run(*args: object, **kwargs: object) -> None:
        """Simulate a Git subprocess that exceeds the deadline."""
        raise subprocess.TimeoutExpired(["git", "ls-remote"], 10)

    monkeypatch.setattr(
        plonk_adapters.shutil,
        "which",
        lambda name: "/usr/bin/git",
    )
    monkeypatch.setattr(plonk_adapters.subprocess, "run", fail_run)

    state = plonk._GitWorktreeAdapter(repo).upstream_state(branch)

    assert state == "unknown", (
        "expected a timed-out upstream query to be reported as unknown"
    )
    assert recording_recorder.span_operations() == ["upstream_lookup"], (
        "expected the ls-remote process boundary to be timed"
    )
    assert [
        (observation.operation, observation.outcome)
        for observation in recording_recorder.observations
    ] == [("upstream_lookup", "failure")], (
        "expected the timeout to record one bounded failure"
    )
    assert recording_recorder.error_kinds("upstream_lookup") == ["os_error"], (
        "expected the timeout to name the OS error class"
    )
    assert recording_recorder.unbounded_values() == set(), (
        "every recorded value comes from a declared vocabulary"
    )


def test_upstream_state_records_a_failed_query_as_a_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_recorder: RecordingRecorder,
) -> None:
    """A non-zero exit other than "no match" is a failed query, not a success."""
    repo, _remote_path, branch = _repository_with_tracking(tmp_path, "present")

    def fail_run(
        arguments: typ.Sequence[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        """Report the generic Git failure status for the query."""
        return subprocess.CompletedProcess(arguments, 128, "", "fatal: unreachable")

    monkeypatch.setattr(
        plonk_adapters.shutil,
        "which",
        lambda name: "/usr/bin/git",
    )
    monkeypatch.setattr(plonk_adapters.subprocess, "run", fail_run)

    state = plonk._GitWorktreeAdapter(repo).upstream_state(branch)

    assert state == "unknown", "expected a failed query to be reported as unknown"
    assert [
        (observation.operation, observation.outcome)
        for observation in recording_recorder.observations
    ] == [("upstream_lookup", "failure")], (
        "expected the failed query to record one bounded failure"
    )
    assert recording_recorder.error_kinds("upstream_lookup") == ["git_command_error"], (
        "expected the failed query to name the Git error class"
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


def test_exact_ref_match_rejects_a_longer_branch_tail() -> None:
    """``ls-remote`` patterns match a ref tail, so presence needs an exact check."""
    output = "abc123\trefs/heads/other/refs/heads/issue-123-fix\n"

    assert not plonk._GitWorktreeAdapter._mentions_exact_ref(
        output, "refs/heads/issue-123-fix"
    ), "expected a longer ref with the same tail not to count as present"
    assert plonk._GitWorktreeAdapter._mentions_exact_ref(
        f"{output}def456\trefs/heads/issue-123-fix\n",
        "refs/heads/issue-123-fix",
    ), "expected the exact ref to count as present"


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


def _repository_with_tracking(
    tmp_path: Path,
    upstream: str,
) -> tuple[Repo, Path, str]:
    """Create a repository whose local branch tracks ``upstream``.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory that owns the repository and its remote.
    upstream : str
        State to leave the upstream in: ``unset``, ``present``, ``gone``, or
        ``unknown``.

    Returns
    -------
    tuple[Repo, Path, str]
        The repository, its bare remote path, and the local branch name.

    """
    repo, remote = git_repo_helpers.repo_with_remote_default(
        tmp_path / "local", tmp_path / "remote.git"
    )
    branch = _WORKTREE_BRANCH
    repo.git.branch(branch)
    if upstream == "unset":
        return repo, Path(remote.git_dir), branch
    if upstream == "unknown":
        repo.git.config(f"branch.{branch}.remote", "origin")
        repo.git.config(f"branch.{branch}.merge", f"refs/heads/{branch}")
        remote_path = Path(remote.git_dir)
        shutil.rmtree(remote_path)
        return repo, remote_path, branch

    repo.git.push("origin", f"{branch}:refs/heads/{branch}")
    repo.git.fetch("origin")
    repo.git.branch("--set-upstream-to", f"origin/{branch}", branch)
    if upstream == "gone":
        repo.git.push("origin", "--delete", branch)
    return repo, Path(remote.git_dir), branch


@pytest.mark.parametrize(
    "upstream",
    [
        pytest.param("unset", id="no-upstream-configured"),
        pytest.param("present", id="upstream-still-exists"),
        pytest.param("gone", id="upstream-deleted"),
        pytest.param("unknown", id="remote-unreachable"),
    ],
)
def test_upstream_state_preserves_every_distinction(
    tmp_path: Path,
    upstream: plonk_records.UpstreamState,
) -> None:
    """The adapter should distinguish unset, present, gone, and unknown."""
    repo, _remote_path, branch = _repository_with_tracking(tmp_path, upstream)
    adapter = plonk._GitWorktreeAdapter(repo)

    state = adapter.upstream_state(branch)

    assert state == upstream, (
        f"expected the adapter to report {upstream!r}, not {state!r}"
    )


def test_upstream_state_treats_a_partial_configuration_as_unset(
    tmp_path: Path,
) -> None:
    """A branch with a remote but no merge ref was never fully pushed."""
    repo = git_repo_helpers.seed_repo(tmp_path / "repo")
    branch = _WORKTREE_BRANCH
    repo.git.branch(branch)
    repo.git.config(f"branch.{branch}.remote", "origin")

    state = plonk._GitWorktreeAdapter(repo).upstream_state(branch)

    assert state == "unset", (
        "expected a half-configured upstream to be treated as never pushed"
    )


def test_upstream_state_rejects_a_non_head_merge_ref(tmp_path: Path) -> None:
    """A merge ref outside ``refs/heads`` cannot be queried as a branch."""
    repo = git_repo_helpers.seed_repo(tmp_path / "repo")
    branch = _WORKTREE_BRANCH
    repo.git.branch(branch)
    repo.git.config(f"branch.{branch}.remote", "origin")
    repo.git.config(f"branch.{branch}.merge", "refs/other/not-a-branch")

    state = plonk._GitWorktreeAdapter(repo).upstream_state(branch)

    assert state == "unknown", "expected a non-head merge ref to be reported as unknown"


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
