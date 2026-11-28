"""Unit tests for ``git-plonk``'s upstream-state query.

The opt-in ``--upstream-gone`` tier treats a deleted upstream as completion
evidence, so this module pins the adapter's reading of Git's upstream
configuration and of ``git ls-remote``. It covers which configured value Git
actually uses, the states that must stay ``unknown``, the bounded deadline, and
the observation each query leaves behind. The worktree preflight and mutation
boundaries are covered in ``test_plonk_worktree_adapter.py``.
"""

from __future__ import annotations

import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]  # asserts timeout mapping
import typing as typ
from pathlib import Path

import pytest
from git import Repo

from git_donkey import plonk, plonk_adapters, plonk_records
from tests import git_repo_helpers

if typ.TYPE_CHECKING:
    from tests.observability_helpers import RecordingRecorder

# The branch and directory ``git donkey`` would have created for the candidate
# worktrees these tests exercise.
_WORKTREE_BRANCH = "issue-123-fix"


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


def test_upstream_state_rejects_an_empty_first_merge_value(tmp_path: Path) -> None:
    """Git does not fall through to a later merge value when the first is empty."""
    repo, _remote_path, branch = _repository_with_tracking(tmp_path, "present")
    repo.git.config("--unset-all", f"branch.{branch}.merge")
    repo.git.config("--add", f"branch.{branch}.merge", "")
    repo.git.config("--add", f"branch.{branch}.merge", f"refs/heads/{branch}")

    state = plonk._GitWorktreeAdapter(repo).upstream_state(branch)

    assert state == "unknown", (
        "expected an empty first merge value to leave the upstream unresolved"
    )


@pytest.mark.parametrize(
    "merge_ref",
    [
        pytest.param("refs/heads/topic*", id="star-wildcard"),
        pytest.param("refs/heads/topic?", id="question-wildcard"),
        pytest.param("refs/heads/topic[0]", id="class-wildcard"),
        pytest.param("refs/tags/v1.0.0", id="not-a-branch"),
        pytest.param("refs/heads/.draft", id="dot-leading-component"),
        pytest.param("refs/heads/topic.lock", id="lock-suffix"),
        pytest.param("refs/heads/a//b", id="empty-component"),
    ],
)
def test_upstream_state_rejects_a_non_exact_merge_ref(
    tmp_path: Path,
    merge_ref: str,
) -> None:
    """A merge ref ls-remote would treat as a pattern fails closed."""
    repo, _remote_path, branch = _repository_with_tracking(tmp_path, "present")
    repo.git.config(f"branch.{branch}.merge", merge_ref)

    state = plonk._GitWorktreeAdapter(repo).upstream_state(branch)

    assert state == "unknown", (
        "expected a merge ref that does not name one branch to be unknown"
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
