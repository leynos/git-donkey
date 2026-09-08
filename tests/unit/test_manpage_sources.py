"""Check manual coverage and the declarative manpage build contract."""

from __future__ import annotations

import ast
import configparser
import shlex
import tomllib
import typing as typ
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _project_config() -> dict[str, typ.Any]:
    """Read heterogeneous TOML; the tests validate the relevant nested shapes."""
    return tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_every_console_script_has_a_manual() -> None:
    """Require exactly one section-one source for each installed command."""
    commands = set(_project_config()["project"]["scripts"])
    sources = {path.stem for path in (_ROOT / "docs/man").glob("*.rst")}
    assert sources == commands


def test_shared_data_maps_only_generated_manuals() -> None:
    """Put generated pages in the wheel data scheme, not site-packages."""
    config = _project_config()
    expected = {
        f"docs/man/{command}.1": f"share/man/man1/{command}.1"
        for command in config["project"]["scripts"]
    }
    assert (
        config["tool"]["hatch"]["build"]["targets"]["wheel"]["shared-data"] == expected
    )


def test_build_commands_generate_every_manual() -> None:
    """Keep the generator, source inventory, and packaging map in agreement."""
    config = _project_config()
    hook = config["tool"]["hatch"]["build"]["targets"]["wheel"]["hooks"][
        "build-scripts"
    ]
    (script,) = hook["scripts"]
    assert script["work_dir"] == "docs/man"
    assert script["out_dir"] == "docs/man"
    assert script["clean_artifacts"] is False
    assert script["clean_out_dir"] is False
    assert script["artifacts"] == []
    commands = {tuple(shlex.split(command)) for command in script["commands"]}
    assert commands == {
        (
            "rst2man",
            "--config=docutils.conf",
            f"--output={command}.1",
            f"{command}.rst",
        )
        for command in config["project"]["scripts"]
    }


def test_generation_dependencies_are_build_only() -> None:
    """Do not add documentation tools to installed CLI dependencies."""
    config = _project_config()
    for dependency in ("docutils", "hatch-build-scripts"):
        assert any(
            requirement.startswith(dependency)
            for requirement in config["build-system"]["requires"]
        )
        assert not any(
            requirement.startswith(dependency)
            for requirement in config["project"]["dependencies"]
        )


def test_generation_is_strict_and_does_not_insert_files() -> None:
    """Fail on malformed sources without including build-host files or dates."""
    config = configparser.ConfigParser()
    config.read(_ROOT / "docs/man/docutils.conf", encoding="utf-8")
    assert config["general"]["halt-level"] == "warning"
    assert config["general"]["exit-status-level"] == "warning"
    assert config["general"]["datestamp"] == ""
    assert not config["general"].getboolean("generator")
    assert not config["parsers"].getboolean("file-insertion-enabled")
    assert not config["parsers"].getboolean("raw-enabled")


@pytest.mark.parametrize(
    "command",
    ("git-donkey", "git-track", "git-fafo", "git-plonk", "git-donkey-template"),
)
def test_manual_has_standard_sections(command: str) -> None:
    """Keep each generated page useful as a standalone reference."""
    source = (_ROOT / f"docs/man/{command}.rst").read_text(encoding="utf-8")
    assert source.startswith(f"{command}\n{'=' * len(command)}\n")
    lines = source.splitlines()
    assert len(lines[3]) >= len(lines[2])
    assert ":Manual section: 1" in source
    for heading in ("SYNOPSIS", "DESCRIPTION", "OPTIONS", "EXAMPLES", "SEE ALSO"):
        assert f"\n{heading}\n" in source
    assert "--help" in source
    assert "--version" in source


@pytest.mark.parametrize(
    ("command", "required_text"),
    (
        ("git-donkey", "--no-pull"),
        ("git-donkey", "ORIGIN_BRANCH"),
        ("git-track", "BRANCH"),
        ("git-fafo", "--trust"),
        ("git-fafo", "--yes"),
        ("git-fafo", "GIT_DONKEY_CREDENTIALS_FILE"),
        ("git-plonk", "--soft"),
        ("git-plonk", "--hard"),
        ("git-plonk", "--dry-run"),
        ("git-donkey-template", "XDG_DATA_HOME"),
    ),
)
def test_manual_covers_command_specific_behaviour(
    command: str,
    required_text: str,
) -> None:
    """Preserve important options and configuration in the installed reference."""
    source = (_ROOT / f"docs/man/{command}.rst").read_text(encoding="utf-8")
    assert required_text in source


@pytest.mark.parametrize(
    ("command", "wrapper"),
    (
        ("git-donkey", "_donkey_cli"),
        ("git-track", "_track_cli"),
        ("git-fafo", "_fafo_cli"),
        ("git-plonk", "_plonk_cli"),
        ("git-donkey-template", "_template_cli"),
    ),
)
def test_manual_covers_cli_parameters(command: str, wrapper: str) -> None:
    """Detect argument and primary-option drift without importing workflows."""
    module = ast.parse((_ROOT / "git_donkey/cli.py").read_text(encoding="utf-8"))
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == wrapper
    )
    source = (_ROOT / f"docs/man/{command}.rst").read_text(encoding="utf-8")
    for argument in function.args.args:
        assert argument.arg.upper() in source
    for argument in function.args.kwonlyargs:
        assert "--" + argument.arg.replace("_", "-") in source
