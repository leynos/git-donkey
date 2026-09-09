"""Real Git tests for explicit rebase and fast-forward-only base updates."""

from __future__ import annotations

import typing as typ

import pytest
from git import Repo

from git_donkey import donkey
from tests.integration.conftest import _seed_repo, _setup_repo

if typ.TYPE_CHECKING:
    from pathlib import Path


def _behind_repo(tmp_path: Path) -> Repo:
    """Create a local main one commit behind its remote counterpart."""
    local_path, _remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)
    _seed_repo(repo, "upstream.txt", "upstream change")
    repo.remote("origin").push("main")
    repo.git.reset("--hard", "HEAD~1")
    return repo


def test_pull_rebase_preserves_local_commits_on_explicit_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit rebase incorporates remote commits and replays local work."""
    repo = _behind_repo(tmp_path)
    _seed_repo(repo, "local.txt", "local change")
    old_tip = repo.head.commit.hexsha
    monkeypatch.chdir(repo.working_tree_dir or ".")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", lambda *_: True)

    assert (
        donkey.run_git_donkey(
            "feature/rebased", ".", options=donkey._PullOptions(pull_rebase=True)
        )
        == 0
    )

    assert repo.head.commit.hexsha != old_tip
    assert (
        repo.git.merge_base("main", "origin/main") == repo.commit("origin/main").hexsha
    )
    assert repo.git.show("main:local.txt") == "local change"
    assert repo.git.show("main:upstream.txt") == "upstream change"
    assert repo.commit("feature/rebased").hexsha == repo.head.commit.hexsha


def test_pull_ff_refuses_divergence_even_with_rebase_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fast-forward mode must neither merge nor rebase divergent local work."""
    repo = _behind_repo(tmp_path)
    _seed_repo(repo, "local.txt", "local change")
    original_tip = repo.head.commit.hexsha
    repo.git.config("pull.rebase", "true")
    repo.git.config("pull.ff", "false")
    monkeypatch.chdir(repo.working_tree_dir or ".")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", lambda *_: True)

    with pytest.raises(SystemExit) as excinfo:
        donkey.run_git_donkey(
            "feature/ff", ".", options=donkey._PullOptions(pull_ff=True)
        )

    assert excinfo.value.code == 1
    assert "pull --ff-only" in capsys.readouterr().err
    assert repo.head.commit.hexsha == original_tip
    assert "feature/ff" not in repo.heads
    assert not repo.is_dirty(untracked_files=True)


@pytest.mark.parametrize(
    "options",
    [donkey._PullOptions(pull_rebase=True), donkey._PullOptions(pull_ff=True)],
)
def test_opt_in_never_pulls_base_into_unrelated_primary_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    options: donkey._PullOptions,
) -> None:
    """An unowned base must not be pulled into the primary worktree's branch."""
    repo = _behind_repo(tmp_path)
    repo.git.checkout("-b", "unrelated")
    _seed_repo(repo, "unrelated.txt", "unrelated change")
    original_tip = repo.head.commit.hexsha
    monkeypatch.chdir(repo.working_tree_dir or ".")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", lambda *_: True)

    with pytest.raises(SystemExit) as excinfo:
        donkey.run_git_donkey("feature/unsafe", "main", options=options)

    assert excinfo.value.code == 1
    assert "not checked out in a worktree" in capsys.readouterr().err
    assert repo.active_branch.name == "unrelated"
    assert repo.head.commit.hexsha == original_tip
    assert "feature/unsafe" not in repo.heads


@pytest.mark.parametrize(
    "options",
    [donkey._PullOptions(pull_rebase=True), donkey._PullOptions(pull_ff=True)],
)
def test_declined_prompt_preserves_explicit_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    options: donkey._PullOptions,
) -> None:
    """Declining an opted-in update keeps the existing base commit."""
    repo = _behind_repo(tmp_path)
    original_tip = repo.head.commit.hexsha
    monkeypatch.chdir(repo.working_tree_dir or ".")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", lambda *_: False)

    assert donkey.run_git_donkey("feature/declined", ".", options=options) == 0

    assert repo.head.commit.hexsha == original_tip
    assert repo.commit("feature/declined").hexsha == original_tip


@pytest.mark.parametrize(
    "options",
    [donkey._PullOptions(pull_rebase=True), donkey._PullOptions(pull_ff=True)],
)
def test_pull_updates_linked_base_and_leaves_primary_alone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    options: donkey._PullOptions,
) -> None:
    """Pulling targets the base's linked worktree, not the calling checkout."""
    repo = _behind_repo(tmp_path)
    repo.git.checkout("-b", "unrelated")
    _seed_repo(repo, "unrelated.txt", "unrelated change")
    original_tip = repo.head.commit.hexsha
    base_path = tmp_path / "base-worktree"
    repo.git.worktree("add", str(base_path), "main")
    monkeypatch.chdir(repo.working_tree_dir or ".")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", lambda *_: True)

    assert donkey.run_git_donkey("feature/linked", "main", options=options) == 0

    assert repo.active_branch.name == "unrelated"
    assert repo.head.commit.hexsha == original_tip
    assert Repo(base_path).head.commit.hexsha == repo.commit("origin/main").hexsha
    assert repo.commit("feature/linked").hexsha == repo.commit("origin/main").hexsha
