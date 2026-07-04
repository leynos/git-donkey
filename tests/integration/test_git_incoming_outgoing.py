"""Integration tests for Mercurial-style incoming and outgoing commands."""

from __future__ import annotations

import typing as typ

from git import Repo

from git_donkey import incoming_outgoing
from tests.integration.conftest import _configure_repo, _seed_repo, _setup_repo

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _clone_remote(remote_path: Path, clone_path: Path) -> Repo:
    """Clone the bare test remote and configure an author identity."""
    repo = Repo.clone_from(remote_path.as_posix(), clone_path, branch="main")
    _configure_repo(repo)
    return repo


def _set_main_upstream(repo: Repo) -> None:
    """Configure ``main`` to track ``origin/main`` in the test repository."""
    repo.git.branch("--set-upstream-to", "origin/main", "main")


def test_git_incoming_fetches_and_reports_remote_only_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """git-incoming should report commits that would be pulled."""
    local_path, remote_path = _setup_repo(tmp_path)
    local_repo = Repo(local_path)
    _set_main_upstream(local_repo)

    peer_repo = _clone_remote(remote_path, tmp_path / "peer")
    _seed_repo(peer_repo, "remote.txt", "remote")
    peer_repo.remote("origin").push("main")

    monkeypatch.chdir(local_path)
    exit_code = incoming_outgoing.run_git_incoming()

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Seed commit" in captured.out
    assert captured.err == ""


def test_git_incoming_no_changes_returns_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """git-incoming should return 1 when nothing would be pulled."""
    local_path, _remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)
    _set_main_upstream(repo)
    repo.remote("origin").fetch()

    monkeypatch.chdir(local_path)
    exit_code = incoming_outgoing.run_git_incoming()

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err == ""


def test_git_outgoing_reports_local_only_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """git-outgoing should report commits that would be pushed."""
    local_path, _remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)
    _set_main_upstream(repo)
    repo.remote("origin").fetch()
    _seed_repo(repo, "local.txt", "local")

    monkeypatch.chdir(local_path)
    exit_code = incoming_outgoing.run_git_outgoing(fetch=False)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Seed commit" in captured.out
    assert captured.err == ""


def test_git_outgoing_no_changes_returns_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """git-outgoing should return 1 when nothing would be pushed."""
    local_path, _remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)
    _set_main_upstream(repo)
    repo.remote("origin").fetch()

    monkeypatch.chdir(local_path)
    exit_code = incoming_outgoing.run_git_outgoing(fetch=False)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err == ""


def test_default_ref_requires_upstream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Default comparison should fail clearly when no upstream is configured."""
    local_path, _remote_path = _setup_repo(tmp_path)

    monkeypatch.chdir(local_path)
    exit_code = incoming_outgoing.run_git_incoming(fetch=False)

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "no upstream branch configured" in captured.err
    assert "pass a ref" in captured.err


def test_no_fetch_uses_current_remote_tracking_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--no-fetch should compare against the already-known tracking ref."""
    local_path, remote_path = _setup_repo(tmp_path)
    local_repo = Repo(local_path)
    _set_main_upstream(local_repo)
    local_repo.remote("origin").fetch()

    peer_repo = _clone_remote(remote_path, tmp_path / "peer")
    _seed_repo(peer_repo, "remote.txt", "remote")
    peer_repo.remote("origin").push("main")

    monkeypatch.chdir(local_path)
    exit_code = incoming_outgoing.run_git_incoming(fetch=False)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err == ""


def test_explicit_ref_does_not_require_upstream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An explicit comparison ref should work without branch upstream config."""
    local_path, remote_path = _setup_repo(tmp_path)
    local_repo = Repo(local_path)
    local_repo.remote("origin").fetch()

    peer_repo = _clone_remote(remote_path, tmp_path / "peer")
    _seed_repo(peer_repo, "remote.txt", "remote")
    peer_repo.remote("origin").push("main")

    monkeypatch.chdir(local_path)
    exit_code = incoming_outgoing.run_git_incoming("origin/main")

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Seed commit" in captured.out
    assert captured.err == ""
