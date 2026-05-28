"""Integration tests for git-donkey template overlay application."""

from __future__ import annotations

import typing as typ

from git_donkey import donkey, slugs, templates
from tests.integration.conftest import _setup_repo

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_git_donkey_applies_template_overlay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git-donkey should apply template overlay files when template exists."""
    local_path, remote_path = _setup_repo(tmp_path)

    # Set up template directory structure
    remote_url = remote_path.as_posix()
    repo_slug = slugs.slug_dash_adler32(remote_url)

    template_base = tmp_path / "templates"
    template_dir = template_base / repo_slug
    template_dir.mkdir(parents=True)

    # Create template files
    (template_dir / ".editorconfig").write_text("[*]\nindent_size = 2\n")
    (template_dir / "config").mkdir()
    (template_dir / "config" / "settings.json").write_text('{"key": "value"}\n')

    # Mock the template base directory
    monkeypatch.setattr(templates, "_get_template_base_dir", lambda: template_base)

    monkeypatch.chdir(local_path)
    exit_code = donkey.run_git_donkey("feature/template-test", no_pull=True)

    assert exit_code == 0, "expected git-donkey to exit successfully"

    worktree_root = local_path.parent / f"{local_path.name}.worktrees"
    worktree_path = worktree_root / "feature/template-test"

    # Verify template files were copied
    assert (worktree_path / ".editorconfig").exists(), (
        "expected .editorconfig to be copied from template"
    )
    assert (worktree_path / ".editorconfig").read_text() == "[*]\nindent_size = 2\n", (
        "expected .editorconfig content to match template"
    )
    assert (worktree_path / "config" / "settings.json").exists(), (
        "expected nested config file to be copied from template"
    )
    assert (worktree_path / "config" / "settings.json").read_text() == (
        '{"key": "value"}\n'
    ), "expected settings.json content to match template"


def test_git_donkey_without_template_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git-donkey should work normally when no template exists."""
    local_path, _remote_path = _setup_repo(tmp_path)

    # No template directory created
    template_base = tmp_path / "templates"
    monkeypatch.setattr(templates, "_get_template_base_dir", lambda: template_base)

    monkeypatch.chdir(local_path)
    exit_code = donkey.run_git_donkey("feature/no-template", no_pull=True)

    assert exit_code == 0, "expected git-donkey to exit successfully without template"

    worktree_root = local_path.parent / f"{local_path.name}.worktrees"
    worktree_path = worktree_root / "feature/no-template"
    assert worktree_path.exists(), "expected worktree to be created"
