"""Integration tests for Mercurial-style incoming and outgoing commands."""

from __future__ import annotations

import sys
import typing as typ

import pytest
from git import Repo

from git_donkey import cli, incoming_outgoing
from tests.git_repo_helpers import configure_repo
from tests.integration.conftest import _seed_repo, _setup_repo

if typ.TYPE_CHECKING:
    from pathlib import Path

    from tests.observability_helpers import RecordingRecorder


# git-donkey's own exit code for a command that could not run, as opposed to
# Mercurial's ``0`` (commits found) and ``1`` (comparison found nothing).
_COULD_NOT_RUN_EXIT_CODE = 2


def _clone_remote(remote_path: Path, clone_path: Path) -> Repo:
    """Clone the bare test remote and configure an author identity."""
    repo = Repo.clone_from(remote_path.as_posix(), clone_path, branch="main")
    configure_repo(repo)
    return repo


def _set_main_upstream(repo: Repo) -> None:
    """Configure ``main`` to track ``origin/main`` in the test repository."""
    repo.git.branch("--set-upstream-to", "origin/main", "main")


class _ComparisonRunner(typ.Protocol):
    """Callable surface shared by the incoming and outgoing runners."""

    def __call__(self, ref: str | None = None, *, fetch: bool = True) -> int:
        """Run one comparison and return its process exit code."""


class _RunAndCapture(typ.Protocol):
    """Callable surface of the runner that captures comparison output."""

    def __call__(
        self,
        path: Path,
        runner: _ComparisonRunner,
        ref: str | None = None,
        *,
        fetch: bool = True,
    ) -> tuple[int, str, str]:
        """Run ``runner`` in ``path`` and return its exit code and output."""


@pytest.fixture
def run_and_capture(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> _RunAndCapture:
    """Return a comparison runner that captures output in its repository."""

    def _run(
        path: Path,
        runner: _ComparisonRunner,
        ref: str | None = None,
        *,
        fetch: bool = True,
    ) -> tuple[int, str, str]:
        monkeypatch.chdir(path)
        exit_code = runner(ref, fetch=fetch)
        captured = capsys.readouterr()
        return exit_code, captured.out, captured.err

    return _run


def test_git_incoming_fetches_and_reports_remote_only_commit(
    tmp_path: Path,
    run_and_capture: _RunAndCapture,
) -> None:
    """git-incoming should report commits that would be pulled."""
    local_path, remote_path = _setup_repo(tmp_path)
    local_repo = Repo(local_path)
    _set_main_upstream(local_repo)

    peer_repo = _clone_remote(remote_path, tmp_path / "peer")
    _seed_repo(peer_repo, "remote.txt", "remote")
    peer_repo.remote("origin").push("main")
    remote_commit = peer_repo.head.commit.hexsha[:7]

    exit_code, out, err = run_and_capture(
        local_path,
        incoming_outgoing.run_git_incoming,
    )

    assert exit_code == 0, "a remote-only commit must exit 0"
    assert remote_commit in out, "incoming must print the fetched remote-only commit"
    assert not err, "a successful comparison must not write to stderr"


def test_git_incoming_no_changes_returns_one(
    tmp_path: Path,
    run_and_capture: _RunAndCapture,
) -> None:
    """git-incoming should return 1 when nothing would be pulled."""
    local_path, _remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)
    _set_main_upstream(repo)
    repo.remote("origin").fetch()

    exit_code, out, err = run_and_capture(
        local_path,
        incoming_outgoing.run_git_incoming,
    )

    assert exit_code == 1, "no incoming commits must exit 1"
    assert not out, "an empty incoming comparison must print nothing"
    assert not err, "an empty incoming comparison must not write to stderr"


def test_git_outgoing_reports_local_only_commit(
    tmp_path: Path,
    run_and_capture: _RunAndCapture,
) -> None:
    """git-outgoing should report commits that would be pushed."""
    local_path, _remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)
    _set_main_upstream(repo)
    repo.remote("origin").fetch()
    _seed_repo(repo, "local.txt", "local")
    local_commit = repo.head.commit.hexsha[:7]

    exit_code, out, err = run_and_capture(
        local_path,
        incoming_outgoing.run_git_outgoing,
        fetch=False,
    )

    assert exit_code == 0, "a local-only commit must exit 0"
    assert local_commit in out, "outgoing must print the local-only commit"
    assert not err, "a successful comparison must not write to stderr"


def test_git_outgoing_no_changes_returns_one(
    tmp_path: Path,
    run_and_capture: _RunAndCapture,
) -> None:
    """git-outgoing should return 1 when nothing would be pushed."""
    local_path, _remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)
    _set_main_upstream(repo)
    repo.remote("origin").fetch()

    exit_code, out, err = run_and_capture(
        local_path,
        incoming_outgoing.run_git_outgoing,
        fetch=False,
    )

    assert exit_code == 1, "no outgoing commits must exit 1"
    assert not out, "an empty outgoing comparison must print nothing"
    assert not err, "an empty outgoing comparison must not write to stderr"


def test_default_ref_requires_upstream(
    tmp_path: Path,
    run_and_capture: _RunAndCapture,
) -> None:
    """Default comparison should fail clearly when no upstream is configured."""
    local_path, _remote_path = _setup_repo(tmp_path)

    exit_code, out, err = run_and_capture(
        local_path,
        incoming_outgoing.run_git_incoming,
        fetch=False,
    )

    assert exit_code == _COULD_NOT_RUN_EXIT_CODE, "a missing upstream must exit 2"
    assert not out, "a missing upstream must print nothing"
    assert "no upstream branch configured" in err, (
        "a missing upstream must explain the configuration error"
    )
    assert "pass a ref" in err, "a missing upstream must suggest passing a ref"


def test_no_fetch_uses_current_remote_tracking_ref(
    tmp_path: Path,
    run_and_capture: _RunAndCapture,
) -> None:
    """--no-fetch should compare against the already-known tracking ref."""
    local_path, remote_path = _setup_repo(tmp_path)
    local_repo = Repo(local_path)
    _set_main_upstream(local_repo)
    local_repo.remote("origin").fetch()

    peer_repo = _clone_remote(remote_path, tmp_path / "peer")
    _seed_repo(peer_repo, "remote.txt", "remote")
    peer_repo.remote("origin").push("main")

    exit_code, out, err = run_and_capture(
        local_path,
        incoming_outgoing.run_git_incoming,
        fetch=False,
    )

    assert exit_code == 1, "--no-fetch must miss commits pushed since the fetch"
    assert not out, "an unchanged tracking ref must print nothing"
    assert not err, "a successful comparison must not write to stderr"


def test_explicit_ref_does_not_require_upstream(
    tmp_path: Path,
    run_and_capture: _RunAndCapture,
) -> None:
    """An explicit comparison ref should work without branch upstream config."""
    local_path, remote_path = _setup_repo(tmp_path)
    local_repo = Repo(local_path)
    local_repo.remote("origin").fetch()

    peer_repo = _clone_remote(remote_path, tmp_path / "peer")
    _seed_repo(peer_repo, "remote.txt", "remote")
    peer_repo.remote("origin").push("main")
    remote_commit = peer_repo.head.commit.hexsha[:7]

    exit_code, out, err = run_and_capture(
        local_path,
        incoming_outgoing.run_git_incoming,
        "origin/main",
    )

    assert exit_code == 0, "an explicit ref must compare successfully"
    assert remote_commit in out, "incoming must print the remote-only commit"
    assert not err, "a successful comparison must not write to stderr"


def test_canonical_ref_fetches_owning_remote(
    tmp_path: Path,
    run_and_capture: _RunAndCapture,
) -> None:
    """A canonical refs/remotes ref should fetch its owning remote first."""
    local_path, remote_path = _setup_repo(tmp_path)
    local_repo = Repo(local_path)
    _set_main_upstream(local_repo)
    local_repo.remote("origin").fetch()

    peer_repo = _clone_remote(remote_path, tmp_path / "peer")
    _seed_repo(peer_repo, "remote.txt", "remote")
    peer_repo.remote("origin").push("main")
    remote_commit = peer_repo.head.commit.hexsha[:7]

    exit_code, out, err = run_and_capture(
        local_path,
        incoming_outgoing.run_git_incoming,
        "refs/remotes/origin/main",
    )

    assert exit_code == 0, "canonical refs must fetch and report new commits"
    assert remote_commit in out, "the fetched canonical ref must be reported"
    assert not err, "a successful comparison must not write to stderr"


def test_git_incoming_entrypoint_reports_remote_only_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    recording_recorder: RecordingRecorder,
) -> None:
    """The git-incoming entrypoint should run the command boundary end to end."""
    local_path, remote_path = _setup_repo(tmp_path)
    local_repo = Repo(local_path)
    local_repo.remote("origin").fetch()

    peer_repo = _clone_remote(remote_path, tmp_path / "peer")
    _seed_repo(peer_repo, "remote.txt", "remote")
    peer_repo.remote("origin").push("main")
    remote_commit = peer_repo.head.commit.hexsha[:7]

    # No upstream is configured, so the exit code proves the explicit ref
    # parsed from ``argv`` reached the runner.
    monkeypatch.chdir(local_path)
    monkeypatch.setattr(sys, "argv", ["git-incoming", "origin/main"])

    with pytest.raises(SystemExit) as exit_info:
        cli.git_incoming()

    captured = capsys.readouterr()
    assert exit_info.value.code == 0, "a remote-only commit must exit 0"
    assert remote_commit in captured.out, (
        "the entrypoint must print the remote-only commit"
    )
    assert not captured.err, "a successful comparison must not write to stderr"
    assert [span.operation for span in recording_recorder.spans] == [
        "comparison_fetch",
        "comparison",
    ], "the entrypoint must time the fetch and the comparison"


def test_git_in_alias_entrypoint_reports_nothing_to_pull(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The git-in alias should run through the same command boundary."""
    local_path, _remote_path = _setup_repo(tmp_path)

    # The ref and the flag both come from ``argv``: an ignored argument list
    # would fall back to the missing upstream and exit 2.
    monkeypatch.chdir(local_path)
    monkeypatch.setattr(sys, "argv", ["git-in", "main", "--no-fetch"])

    with pytest.raises(SystemExit) as exit_info:
        cli.git_in()

    captured = capsys.readouterr()
    assert exit_info.value.code == 1, "nothing to pull must exit 1"
    assert not captured.out, "an empty comparison must print nothing"
    assert not captured.err, "an empty comparison must not write to stderr"


def test_git_outgoing_entrypoint_reports_local_only_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    recording_recorder: RecordingRecorder,
) -> None:
    """The git-outgoing entrypoint should run the command boundary end to end."""
    local_path, _remote_path = _setup_repo(tmp_path)
    local_repo = Repo(local_path)
    local_repo.remote("origin").fetch()
    _seed_repo(local_repo, "local.txt", "local")
    local_commit = local_repo.head.commit.hexsha[:7]

    # No upstream is configured, so the exit code proves the explicit ref
    # parsed from ``argv`` reached the runner: an ignored argument list would
    # fall back to the missing upstream and exit 2. An ignored ``--no-fetch``
    # would fetch instead, which the recorded spans below detect.
    monkeypatch.chdir(local_path)
    monkeypatch.setattr(sys, "argv", ["git-outgoing", "origin/main", "--no-fetch"])

    with pytest.raises(SystemExit) as exit_info:
        cli.git_outgoing()

    captured = capsys.readouterr()
    assert exit_info.value.code == 0, "a local-only commit must exit 0"
    assert local_commit in captured.out, (
        "the entrypoint must print the local-only commit"
    )
    assert not captured.err, "a successful comparison must not write to stderr"
    assert [span.operation for span in recording_recorder.spans] == ["comparison"], (
        "--no-fetch must time the comparison without timing a fetch"
    )


def test_git_out_alias_entrypoint_reports_local_only_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    recording_recorder: RecordingRecorder,
) -> None:
    """The git-out alias should run through the same command boundary."""
    local_path, _remote_path = _setup_repo(tmp_path)
    local_repo = Repo(local_path)
    local_repo.remote("origin").fetch()
    _seed_repo(local_repo, "local.txt", "local")
    local_commit = local_repo.head.commit.hexsha[:7]

    monkeypatch.chdir(local_path)
    monkeypatch.setattr(sys, "argv", ["git-out", "origin/main", "--no-fetch"])

    with pytest.raises(SystemExit) as exit_info:
        cli.git_out()

    captured = capsys.readouterr()
    assert exit_info.value.code == 0, "a local-only commit must exit 0"
    assert local_commit in captured.out, "the alias must print the local-only commit"
    assert not captured.err, "a successful comparison must not write to stderr"
    assert recording_recorder.outcomes("comparison") == ["found"], (
        "the alias must record the comparison outcome"
    )


def test_fetch_failure_returns_two(
    tmp_path: Path,
    run_and_capture: _RunAndCapture,
) -> None:
    """A failed fetch should exit 2 rather than report an empty comparison."""
    local_path, _remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)
    _set_main_upstream(repo)
    repo.git.remote("set-url", "origin", (tmp_path / "missing.git").as_posix())

    exit_code, out, err = run_and_capture(
        local_path,
        incoming_outgoing.run_git_incoming,
    )

    assert exit_code == _COULD_NOT_RUN_EXIT_CODE, "a failed fetch must exit 2, not 1"
    assert not out, "a failed fetch must print no commits"
    assert "fetch failed" in err, "a failed fetch must be reported on stderr"
