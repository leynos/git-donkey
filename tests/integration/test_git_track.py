"""Integration tests for git-track branch synchronization."""

from __future__ import annotations

import typing as typ

from git import Repo

from git_donkey import track
from tests.integration.conftest import _seed_repo, _setup_repo

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_git_track_creates_tracking_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git-track should create and check out a tracking branch."""
    local_path, _remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)

    repo.git.checkout("-b", "feature/track")
    _seed_repo(repo, "feature.txt", "feature")
    repo.remote("origin").push("feature/track")
    repo.git.checkout("main")
    repo.delete_head("feature/track", force=True)

    monkeypatch.chdir(local_path)
    exit_code = track.run_git_track("feature/track")

    assert exit_code == 0, "expected git-track to exit successfully"
    assert Repo(local_path).active_branch.name == "feature/track", (
        "expected feature/track to be checked out"
    )
