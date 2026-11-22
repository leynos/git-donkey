"""Snapshot tests for ``git-fafo`` error messages.

The tests pin complete stderr output for the distinct user-facing failure
paths emitted by ``git_donkey.fafo``, ``git_donkey.fafo_github``, and
``git_donkey.fafo_adoption``. Syrupy stores the raw stream in
``__snapshots__/test_fafo_error_messages.ambr``, so a change to what the user
sees arrives as a reviewable snapshot diff rather than a silent edit.
"""

from __future__ import annotations

import typing as typ

import pytest

from git_donkey import fafo

if typ.TYPE_CHECKING:
    from pathlib import Path

    from syrupy.assertion import SnapshotAssertion


def test_empty_scaffold_existing_path_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    snapshot: SnapshotAssertion,
) -> None:
    """A directory creation race should report a conflict message."""
    repo_path = tmp_path / "demo-repo"
    repo_path.mkdir()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        fafo._scaffold_repo(template=None, repo_name="demo-repo", trust=False)

    assert excinfo.value.code == 1, "existing scaffold path should be a conflict"
    captured = capsys.readouterr()
    assert len(captured.out) == 0, "existing scaffold path should not write to stdout"
    assert captured.err == snapshot, "existing scaffold path message should stay stable"


def test_existing_repository_without_confirmation_message(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    snapshot: SnapshotAssertion,
) -> None:
    """Declining adoption should explain how to confirm a safe adoption."""
    monkeypatch.setattr(fafo.helpers, "_prompt_yes_no", lambda *_args, **_kwargs: False)

    with pytest.raises(SystemExit) as excinfo:
        fafo._confirm_adopt_existing_repository(
            owner="octocat",
            repo_name="demo-repo",
            yes=False,
        )

    assert excinfo.value.code == 1, "declined adoption should be a conflict"
    captured = capsys.readouterr()
    assert len(captured.out) == 0, "declined adoption should not write to stdout"
    assert captured.err == snapshot, "declined adoption message should stay stable"


def test_non_empty_existing_repository_message(
    capsys: pytest.CaptureFixture[str],
    snapshot: SnapshotAssertion,
) -> None:
    """Non-empty remotes should explain that the name cannot be adopted."""
    with pytest.raises(SystemExit) as excinfo:
        fafo._die_existing_not_empty(owner="octocat", repo_name="demo-repo")

    assert excinfo.value.code == 1, "non-empty remote should be a conflict"
    captured = capsys.readouterr()
    assert len(captured.out) == 0, "non-empty remote should not write to stdout"
    assert captured.err == snapshot, "non-empty remote message should stay stable"


def test_missing_token_without_terminal_message(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    snapshot: SnapshotAssertion,
) -> None:
    """Missing credentials in a non-interactive shell should be actionable."""
    monkeypatch.setattr(fafo.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(fafo.sys.stdout, "isatty", lambda: False)

    with pytest.raises(SystemExit) as excinfo:
        fafo._ensure_interactive()

    assert excinfo.value.code == 1, "missing token without terminal should fail"
    captured = capsys.readouterr()
    assert len(captured.out) == 0, "missing token should not write to stdout"
    assert captured.err == snapshot, "missing token message should stay stable"
