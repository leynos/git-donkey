"""Unit tests for git-fafo Copier command construction.

These tests target the local scaffold helpers in ``git_donkey.fafo`` without
touching GitHub or real Git remotes. Broader input-space checks for the same
helpers live in ``test_fafo_properties``.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

from git_donkey import fafo

if typ.TYPE_CHECKING:
    import pytest


def test_copier_copy_command_omits_trust_by_default() -> None:
    """Copier should run without trust unless explicitly requested."""
    command = fafo._copier_copy_command(
        copier_path="copier",
        template="git@example.com:owner/agent-template-python",
        repo_name="demo-repo",
        trust=False,
    )

    assert command == [
        "copier",
        "copy",
        "git@example.com:owner/agent-template-python",
        "demo-repo",
    ]


def test_template_for_language_returns_none_without_language() -> None:
    """Omitting the language should select the empty-project path."""
    assert fafo._template_for_language(owner="octocat", language=None) is None


def test_template_for_language_builds_agent_template_url() -> None:
    """Language-specific scaffolds should use the matching agent template."""
    template = fafo._template_for_language(owner="octocat", language="python")

    assert template == "git@github.com:octocat/agent-template-python"


def test_copier_copy_command_adds_trust_when_requested() -> None:
    """Trusted templates should pass Copier's explicit trust flag."""
    command = fafo._copier_copy_command(
        copier_path="copier",
        template="git@example.com:owner/agent-template-python",
        repo_name="demo-repo",
        trust=True,
    )

    assert command == [
        "copier",
        "copy",
        "--trust",
        "git@example.com:owner/agent-template-python",
        "demo-repo",
    ]


def test_run_copier_interactive_uses_trusted_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The interactive Copier runner should pass trust through to subprocess."""
    recorded: dict[str, object] = {}

    def _fake_run(cmd: list[str], **kwargs: object) -> object:
        recorded["cmd"] = cmd
        recorded["check"] = kwargs["check"]
        return object()

    monkeypatch.setattr(fafo.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(fafo.subprocess, "run", _fake_run)

    fafo._run_copier_interactive(
        template="git@example.com:owner/agent-template-python",
        repo_name="demo-repo",
        trust=True,
    )

    assert recorded["cmd"] == [
        "/usr/bin/copier",
        "copy",
        "--trust",
        "git@example.com:owner/agent-template-python",
        "demo-repo",
    ]
    assert recorded["check"] is True


def test_scaffold_repo_creates_empty_project_without_template(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty project scaffolding should create the repo directory directly."""
    called: dict[str, object] = {}

    def _fake_run_copier_interactive(**kwargs: object) -> None:
        called["kwargs"] = kwargs

    monkeypatch.setattr(
        fafo,
        "_run_copier_interactive",
        _fake_run_copier_interactive,
    )
    monkeypatch.chdir(tmp_path)

    repo_path = fafo._scaffold_repo(template=None, repo_name="demo-repo", trust=True)

    assert repo_path == Path("demo-repo")
    assert (tmp_path / "demo-repo").is_dir()
    assert called == {}
