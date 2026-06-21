"""Integration tests for git-fafo repository scaffolding.

These tests cover the orchestration in ``git_donkey.fafo`` with stubbed
external commands and stubbed GitHub API calls. Shared command-stub setup lives
in ``tests.integration.conftest`` so each test only declares the workflow
variation it needs to verify.
"""

from __future__ import annotations

import os
import typing as typ

from git_donkey import fafo

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from conftest import StubGitHub, StubUser
    from tests.integration.conftest import StubCommands


def test_git_fafo_existing_repo_early_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """git-fafo should exit early if the target repo directory already exists."""
    existing_repo_path = tmp_path / "demo-repo"
    existing_repo_path.mkdir()

    monkeypatch.chdir(tmp_path)

    called: dict[str, bool] = {}

    def _fake_run_fafo_commands(*_: object, **__: object) -> None:
        called["ran"] = True

    monkeypatch.setattr(fafo.helpers, "_require_command", lambda *_: None)
    monkeypatch.setattr(fafo, "_run_fafo_commands", _fake_run_fafo_commands)

    auth_value = "fake-token"
    exit_code = fafo.run_git_fafo("demo-repo", "python", token=auth_value)

    assert exit_code == 1, "expected git-fafo to return error for existing path"
    assert called == {}, "expected no git-fafo commands to run"
    assert "You did that one already!" in capsys.readouterr().err, (
        "expected early-return message on stderr"
    )


def test_git_fafo_runs_expected_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    github_stubs: tuple[type[StubUser], type[StubGitHub]],
    stub_commands: StubCommands,
) -> None:
    """git-fafo should invoke copier, GitHub API, and git with expected arguments."""
    created: dict[str, str | bool] = {}

    stub_github = github_stubs[1]

    def _fake_login(*, token: str | None = None) -> object:
        created["token"] = token or ""
        return stub_github("example", created)

    monkeypatch.setenv(
        "PATH", f"{stub_commands.bin_dir}{os.pathsep}{os.environ['PATH']}"
    )
    monkeypatch.setenv("USER", "example")
    monkeypatch.setenv("STUB_LOG", str(stub_commands.log_path))
    auth_value = "fake-token"
    monkeypatch.setenv("GITHUB_TOKEN", auth_value)
    monkeypatch.setattr(fafo.github3, "login", _fake_login)
    monkeypatch.chdir(tmp_path)

    exit_code = fafo.run_git_fafo("demo-repo", "python", token=auth_value)

    assert exit_code == 0, "expected git-fafo to exit successfully"
    assert (tmp_path / "demo-repo").exists(), "expected repo directory to exist"

    calls = stub_commands.log_path.read_text().splitlines()
    assert (
        calls[0] == "copier copy git@github.com:example/agent-template-python demo-repo"
    ), "expected copier to run with the template and repo name"
    assert "git init" in calls[1], "expected git init to be invoked"
    assert created["token"] == auth_value, "expected token passed to github3 login"
    assert created["name"] == "demo-repo", "expected repo to be created"
    assert created["private"] is False, "expected repo to be public"


def test_git_fafo_trusts_copier_template_when_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    github_stubs: tuple[type[StubUser], type[StubGitHub]],
    stub_commands: StubCommands,
) -> None:
    """git-fafo should pass --trust to Copier for trusted templates."""
    created: dict[str, str | bool] = {}
    stub_github = github_stubs[1]

    def _fake_login(*, token: str | None = None) -> object:
        created["token"] = token or ""
        return stub_github("example", created)

    monkeypatch.setenv(
        "PATH", f"{stub_commands.bin_dir}{os.pathsep}{os.environ['PATH']}"
    )
    monkeypatch.setenv("USER", "example")
    monkeypatch.setenv("STUB_LOG", str(stub_commands.log_path))
    auth_value = "fake-token"
    monkeypatch.setenv("GITHUB_TOKEN", auth_value)
    monkeypatch.setattr(fafo.github3, "login", _fake_login)
    monkeypatch.chdir(tmp_path)

    exit_code = fafo.run_git_fafo(
        "demo-repo",
        "python",
        token=auth_value,
        options=fafo._FafoOptions(trust=True),
    )

    assert exit_code == 0, "expected git-fafo to exit successfully"
    calls = stub_commands.log_path.read_text().splitlines()
    assert (
        calls[0]
        == "copier copy --trust git@github.com:example/agent-template-python demo-repo"
    ), "expected copier to trust the template"


def test_git_fafo_without_language_creates_empty_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    github_stubs: tuple[type[StubUser], type[StubGitHub]],
    stub_commands: StubCommands,
) -> None:
    """git-fafo should create an empty repo when language is omitted."""
    created: dict[str, str | bool] = {}
    stub_github = github_stubs[1]

    def _fake_login(*, token: str | None = None) -> object:
        created["token"] = token or ""
        return stub_github("example", created)

    required_commands: list[str] = []

    def _fake_require_command(command: str, _: str) -> None:
        required_commands.append(command)

    monkeypatch.setenv(
        "PATH", f"{stub_commands.bin_dir}{os.pathsep}{os.environ['PATH']}"
    )
    monkeypatch.setenv("USER", "example")
    monkeypatch.setenv("STUB_LOG", str(stub_commands.log_path))
    auth_value = "fake-token"
    monkeypatch.setenv("GITHUB_TOKEN", auth_value)
    monkeypatch.setattr(fafo.github3, "login", _fake_login)
    monkeypatch.setattr(fafo.helpers, "_require_command", _fake_require_command)
    monkeypatch.chdir(tmp_path)

    exit_code = fafo.run_git_fafo("demo-repo", token=auth_value)

    assert exit_code == 0, "expected git-fafo to exit successfully"
    assert (tmp_path / "demo-repo").is_dir(), "expected empty repo directory"
    assert required_commands == ["git"], "expected no copier requirement"

    calls = stub_commands.log_path.read_text().splitlines()
    assert calls[0] == "git init", "expected git init to be invoked"
    assert not any(call.startswith("copier ") for call in calls), (
        "expected no copier invocation"
    )
    assert created["token"] == auth_value, "expected token passed to github3 login"
    assert created["name"] == "demo-repo", "expected repo to be created"
    assert created["private"] is False, "expected repo to be public"
