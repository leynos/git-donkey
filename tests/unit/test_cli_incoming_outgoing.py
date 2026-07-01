"""Unit tests for incoming and outgoing command-line wrappers."""

from __future__ import annotations

import tomllib
import typing as typ
from pathlib import Path

import pytest

from git_donkey import cli, incoming_outgoing

if typ.TYPE_CHECKING:
    import collections.abc as cabc


def test_incoming_cli_passes_ref_and_no_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """git-incoming CLI options should pass through to the workflow runner."""
    recorded: dict[str, object] = {}

    def _fake_run_git_incoming(ref: str | None = None, *, fetch: bool = True) -> int:
        recorded["ref"] = ref
        recorded["fetch"] = fetch
        return 0

    monkeypatch.setattr(
        incoming_outgoing,
        "run_git_incoming",
        _fake_run_git_incoming,
    )

    with pytest.raises(SystemExit) as excinfo:
        typ.cast("cabc.Callable[[list[str]], None]", cli._incoming_app)([
            "origin/main",
            "--no-fetch",
        ])

    assert excinfo.value.code == 0
    assert recorded == {"ref": "origin/main", "fetch": False}


def test_outgoing_cli_defaults_to_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """git-outgoing should fetch by default and allow omitted refs."""
    recorded: dict[str, object] = {}

    def _fake_run_git_outgoing(ref: str | None = None, *, fetch: bool = True) -> int:
        recorded["ref"] = ref
        recorded["fetch"] = fetch
        return 1

    monkeypatch.setattr(
        incoming_outgoing,
        "run_git_outgoing",
        _fake_run_git_outgoing,
    )

    with pytest.raises(SystemExit) as excinfo:
        typ.cast("cabc.Callable[[list[str]], None]", cli._outgoing_app)([])

    assert excinfo.value.code == 1
    assert recorded == {"ref": None, "fetch": True}


def test_pyproject_registers_incoming_outgoing_aliases() -> None:
    """Console scripts should expose the long and short Git subcommands."""
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert (
        pyproject["project"]["scripts"]
        | {
            "git-incoming": "git_donkey.cli:git_incoming",
            "git-in": "git_donkey.cli:git_in",
            "git-outgoing": "git_donkey.cli:git_outgoing",
            "git-out": "git_donkey.cli:git_out",
        }
        == pyproject["project"]["scripts"]
    )
