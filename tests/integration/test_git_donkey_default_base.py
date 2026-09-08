"""Real-repository regression tests for remote-default worktree bases."""

from __future__ import annotations

import typing as typ

import pytest
from git import Repo

from git_donkey import donkey
from tests.integration.conftest import _seed_repo, _setup_repo

if typ.TYPE_CHECKING:
    from pathlib import Path


def _reject_prompt(_question: str) -> bool:
    """Fail if the non-pulling default asks to update a checkout."""
    pytest.fail("default worktree creation must not prompt for a pull")


def _worktree_repo(local_path: Path, branch: str) -> Repo:
    """Open a worktree created by the workflow under test."""
    return Repo(local_path.parent / "local.worktrees" / branch)


@pytest.mark.parametrize("default_branch", ["main", "trunk", "release/stable"])
@pytest.mark.parametrize("remote_name", ["origin", "upstream"])
def test_default_uses_remote_tip_without_changing_dirty_local_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    default_branch: str,
    remote_name: str,
) -> None:
    """Implicit bases exclude local commits and preserve staged and dirty work."""
    local_path, remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)
    repo.git.branch("-M", default_branch)
    if remote_name != "origin":
        repo.git.remote("rename", "origin", remote_name)
    _seed_repo(repo, "remote.txt", "remote change")
    repo.remote(remote_name).push(default_branch)
    Repo(remote_path).git.symbolic_ref("HEAD", f"refs/heads/{default_branch}")
    remote_tip = repo.head.commit.hexsha
    repo.git.reset("--hard", "HEAD~1")
    _seed_repo(repo, "local.txt", "unpublished local commit")
    local_tip = repo.head.commit.hexsha
    (local_path / "README.md").write_text("staged change")
    repo.index.add(["README.md"])
    index_tree = repo.git.write_tree()
    (local_path / "README.md").write_text("unstaged change")
    (local_path / "untracked.txt").write_text("untracked change")
    repo.git.config("branch.autoSetupMerge", "always")
    monkeypatch.chdir(local_path)
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", _reject_prompt)

    assert donkey.run_git_donkey("feature/default") == 0

    assert repo.head.commit.hexsha == local_tip
    assert repo.active_branch.name == default_branch
    assert repo.git.write_tree() == index_tree
    assert (local_path / "README.md").read_text() == "unstaged change"
    assert (local_path / "untracked.txt").read_text() == "untracked change"
    assert (
        _worktree_repo(local_path, "feature/default").head.commit.hexsha
        == remote_tip
    )
    assert (
        _worktree_repo(local_path, "feature/default").active_branch.tracking_branch()
        is None
    )


def test_remote_head_is_rediscovered_with_a_narrow_fetch_refspec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale remote/HEAD and a main-only fetch must not select the old default."""
    local_path, remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)
    repo.git.symbolic_ref("refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    repo.git.checkout("-b", "trunk")
    _seed_repo(repo, "trunk.txt", "new default")
    repo.remote("origin").push("trunk")
    remote_tip = repo.head.commit.hexsha
    Repo(remote_path).git.symbolic_ref("HEAD", "refs/heads/trunk")
    repo.git.checkout("main")
    repo.git.branch("-D", "trunk")
    repo.git.update_ref("-d", "refs/remotes/origin/trunk")
    repo.git.config("remote.origin.fetch", "+refs/heads/main:refs/remotes/origin/main")
    monkeypatch.chdir(local_path)

    assert donkey.run_git_donkey("feature/new-default") == 0

    assert (
        _worktree_repo(local_path, "feature/new-default").head.commit.hexsha
        == remote_tip
    )
    assert "trunk" not in repo.heads
    assert repo.active_branch.name == "main"


def test_missing_advertised_default_requires_explicit_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Missing remote HEAD is an error, never a fallback to local main."""
    local_path, remote_path = _setup_repo(tmp_path)
    Repo(remote_path).git.symbolic_ref("HEAD", "refs/heads/missing")
    repo = Repo(local_path)
    original_tip = repo.head.commit.hexsha
    monkeypatch.chdir(local_path)

    with pytest.raises(SystemExit) as excinfo:
        donkey.run_git_donkey("feature/missing")

    assert excinfo.value.code == 1
    assert "specify a base branch explicitly" in capsys.readouterr().err
    assert "feature/missing" not in repo.heads
    assert repo.head.commit.hexsha == original_tip
    assert donkey.run_git_donkey("feature/explicit", ".") == 0
    assert (
        _worktree_repo(local_path, "feature/explicit").head.commit.hexsha
        == original_tip
    )


def test_dot_uses_calling_linked_worktree_not_primary_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit dot retains the caller's branch when invoked in a worktree."""
    local_path, _remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)
    caller_path = tmp_path / "caller"
    repo.git.worktree("add", "-b", "caller", str(caller_path), "main")
    caller = Repo(caller_path)
    _seed_repo(caller, "caller.txt", "caller-only content")
    monkeypatch.chdir(caller_path)

    assert donkey.run_git_donkey("feature/from-caller", ".") == 0

    assert (
        _worktree_repo(local_path, "feature/from-caller").head.commit.hexsha
        == caller.head.commit.hexsha
    )
    assert repo.active_branch.name == "main"


def test_principal_remote_keeps_existing_first_configured_rule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adding an origin must not override the existing principal-remote rule."""
    local_path, _remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)
    repo.git.remote("rename", "origin", "upstream")
    other_remote = Repo.init(tmp_path / "other.git", bare=True)
    repo.create_remote("origin", other_remote.git_dir)
    monkeypatch.chdir(local_path)

    assert donkey.run_git_donkey("feature/principal") == 0

    assert (
        _worktree_repo(local_path, "feature/principal").head.commit.hexsha
        == repo.commit("refs/remotes/upstream/main").hexsha
    )
