"""Integration tests for the ``git-donkey-template`` sub-command.

These tests exercise the CLI command in ``git_donkey.cli`` against real
temporary repositories and template directories. They depend on the shared
GitPython setup helpers from ``tests.integration.conftest``.
"""

from __future__ import annotations

import typing as typ

from git import Repo

from git_donkey import slugs, template_cmd, templates
from tests.integration.conftest import _configure_repo, _setup_repo

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_git_donkey_template_creates_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """git-donkey-template should create and display template directory."""
    local_path, remote_path = _setup_repo(tmp_path)

    template_base = tmp_path / "templates"
    monkeypatch.setattr(templates, "_get_template_base_dir", lambda: template_base)

    monkeypatch.chdir(local_path)
    exit_code = template_cmd.run_git_donkey_template()

    assert exit_code == 0, "expected git-donkey-template to exit successfully"

    # Calculate expected path
    remote_url = remote_path.as_posix()
    repo_slug = slugs.slug_dash_adler32(remote_url)
    expected_path = template_base / repo_slug

    assert expected_path.exists(), "expected template directory to be created"
    assert expected_path.is_dir(), "expected template path to be a directory"

    # Check output
    captured = capsys.readouterr()
    assert str(expected_path) in captured.out, (
        "expected template directory path in output"
    )


def test_git_donkey_template_shows_existing_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """git-donkey-template should display existing template directory."""
    local_path, remote_path = _setup_repo(tmp_path)

    # Create template directory with files
    template_base = tmp_path / "templates"
    remote_url = remote_path.as_posix()
    repo_slug = slugs.slug_dash_adler32(remote_url)
    template_dir = template_base / repo_slug
    template_dir.mkdir(parents=True)
    (template_dir / "test.txt").write_text("test content")

    monkeypatch.setattr(templates, "_get_template_base_dir", lambda: template_base)

    monkeypatch.chdir(local_path)
    exit_code = template_cmd.run_git_donkey_template()

    assert exit_code == 0, "expected git-donkey-template to exit successfully"

    # Check output
    captured = capsys.readouterr()
    assert str(template_dir) in captured.out, (
        "expected template directory path in output"
    )
    # Should not show "empty" message since directory has files
    assert "empty" not in captured.err.lower(), (
        "expected no empty message when directory has files"
    )


def test_git_donkey_template_fails_outside_git_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """git-donkey-template should fail with error outside a Git repo."""
    # Create a bare temporary directory (no .git)
    non_repo_dir = tmp_path / "not-a-repo"
    non_repo_dir.mkdir()

    # Change into the non-repo directory
    monkeypatch.chdir(non_repo_dir)

    # Run git-donkey-template; expect SystemExit
    exit_code = None
    try:
        exit_code = template_cmd.run_git_donkey_template()
    except SystemExit as e:
        exit_code = e.code

    assert exit_code != 0, (
        "expected git-donkey-template to exit non-zero outside a Git repo"
    )

    # helpers._die should emit a clear error message on stderr
    captured = capsys.readouterr()
    assert "git repository" in captured.err.lower()


def test_git_donkey_template_fails_without_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """git-donkey-template should error when repository has no remote configured."""
    # Initialize a repo with no remotes
    repo_path = tmp_path / "repo-without-remote"
    repo = Repo.init(repo_path)
    _configure_repo(repo)

    # Ensure the repo has at least one commit
    dummy_file = repo_path / "README.md"
    dummy_file.write_text("initial\n", encoding="utf-8")
    repo.index.add([str(dummy_file)])
    repo.index.commit("Initial commit without remote")

    # Change into the repo directory
    monkeypatch.chdir(repo_path)

    # Run git-donkey-template; expect SystemExit due to missing remote
    exit_code = None
    try:
        exit_code = template_cmd.run_git_donkey_template()
    except SystemExit as e:
        exit_code = e.code

    assert exit_code != 0, "expected non-zero exit code when no remote is configured"

    # Assert that the error message indicates the missing-remote problem
    captured = capsys.readouterr()
    assert "remote" in captured.err.lower()
    assert "template" in captured.err.lower()


def test_git_donkey_template_fails_without_origin_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """git-donkey-template should error when origin remote is missing."""
    repo_path = tmp_path / "repo-multi-remote"
    repo = Repo.init(repo_path)
    _configure_repo(repo)

    dummy_file = repo_path / "README.md"
    dummy_file.write_text("initial\n", encoding="utf-8")
    repo.index.add([str(dummy_file)])
    repo.index.commit("Initial commit")

    upstream_path = tmp_path / "upstream.git"
    fork_path = tmp_path / "fork.git"
    Repo.init(upstream_path, bare=True)
    Repo.init(fork_path, bare=True)

    repo.create_remote("upstream", upstream_path.as_posix())
    repo.create_remote("fork", fork_path.as_posix())

    monkeypatch.chdir(repo_path)

    exit_code = None
    try:
        exit_code = template_cmd.run_git_donkey_template()
    except SystemExit as e:
        exit_code = e.code

    assert exit_code != 0, "expected non-zero exit code when origin remote is missing"

    captured = capsys.readouterr()
    assert "origin" in captured.err.lower()
