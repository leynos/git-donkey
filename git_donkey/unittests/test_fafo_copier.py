"""Unit tests for git-fafo Copier command construction."""

from __future__ import annotations

import typing as typ

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
