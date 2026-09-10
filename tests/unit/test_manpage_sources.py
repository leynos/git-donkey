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


class _ProjectTable(typ.TypedDict):
    """Entries of the ``project`` table the manual contract depends on."""

    scripts: dict[str, str]
    dependencies: list[str]


class _BuildSystemTable(typ.TypedDict):
    """Entries of the ``build-system`` table holding build-only requirements."""

    requires: list[str]


class _BuildScript(typ.TypedDict):
    """One ``hatch-build-scripts`` generator entry."""

    work_dir: str
    out_dir: str
    clean_artifacts: bool
    clean_out_dir: bool
    artifacts: list[str]
    commands: list[str]


class _BuildScriptsHook(typ.TypedDict):
    """The hook table listing the manual generator entries."""

    scripts: list[_BuildScript]


# The hook name contains a hyphen, so this table needs the functional form.
_WheelTarget = typ.TypedDict(
    "_WheelTarget",
    {
        "shared-data": dict[str, str],
        "hooks": dict[str, _BuildScriptsHook],
    },
)


class _HatchBuildTable(typ.TypedDict):
    """The ``hatch.build`` table holding target tables by name."""

    targets: dict[str, _WheelTarget]


class _HatchTable(typ.TypedDict):
    """The ``hatch`` table of the ``tool`` table."""

    build: _HatchBuildTable


class _ToolTable(typ.TypedDict):
    """The ``tool`` table of ``pyproject.toml``."""

    hatch: _HatchTable


# The ``build-system`` key contains a hyphen, so this table needs the functional
# form as well.
_PyProjectTable = typ.TypedDict(
    "_PyProjectTable",
    {
        "project": _ProjectTable,
        "tool": _ToolTable,
        "build-system": _BuildSystemTable,
    },
)


def _project_config() -> _PyProjectTable:
    """Read the ``pyproject.toml`` tables the manual contract depends on."""
    return typ.cast(
        "_PyProjectTable",
        tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8")),
    )


def test_every_console_script_has_a_manual() -> None:
    """Require exactly one section-one source for each installed command."""
    commands = set(_project_config()["project"]["scripts"])
    sources = {path.stem for path in (_ROOT / "docs/man").glob("*.rst")}
    assert sources == commands, (
        f"console scripts without a manual source: {sorted(commands - sources)}; "
        f"manual sources without a console script: {sorted(sources - commands)}"
    )


def test_shared_data_maps_only_generated_manuals() -> None:
    """Put generated pages in the wheel data scheme, not site-packages."""
    config = _project_config()
    expected = {
        f"docs/man/{command}.1": f"share/man/man1/{command}.1"
        for command in config["project"]["scripts"]
    }
    shared_data = config["tool"]["hatch"]["build"]["targets"]["wheel"]["shared-data"]
    assert shared_data == expected, (
        f"wheel shared-data must map exactly the generated manuals: {shared_data}"
    )


def test_build_commands_generate_every_manual() -> None:
    """Keep the generator, source inventory, and packaging map in agreement."""
    config = _project_config()
    hook = config["tool"]["hatch"]["build"]["targets"]["wheel"]["hooks"][
        "build-scripts"
    ]
    (script,) = hook["scripts"]
    assert script["work_dir"] == "docs/man", (
        f"generator must run in docs/man: {script['work_dir']}"
    )
    assert script["out_dir"] == "docs/man", (
        f"generator must write to docs/man: {script['out_dir']}"
    )
    assert script["clean_artifacts"] is False, (
        f"generator cleanup must stay disabled: {script['clean_artifacts']}"
    )
    assert script["clean_out_dir"] is False, (
        f"generator output cleanup must stay disabled: {script['clean_out_dir']}"
    )
    assert script["artifacts"] == [], (
        f"generated pages must not be build artifacts: {script['artifacts']}"
    )
    commands = {tuple(shlex.split(command)) for command in script["commands"]}
    expected = {
        (
            "rst2man",
            "--config=docutils.conf",
            f"--output={command}.1",
            f"{command}.rst",
        )
        for command in config["project"]["scripts"]
    }
    assert commands == expected, (
        f"manuals without a generator command: {sorted(expected - commands)}; "
        f"generator commands without a console script: "
        f"{sorted(commands - expected)}"
    )


def test_generation_dependencies_are_build_only() -> None:
    """Do not add documentation tools to installed CLI dependencies."""
    config = _project_config()
    for dependency in ("docutils", "hatch-build-scripts"):
        requires = config["build-system"]["requires"]
        assert any(requirement.startswith(dependency) for requirement in requires), (
            f"build-system requires must declare {dependency}: {requires}"
        )
        dependencies = config["project"]["dependencies"]
        assert not any(
            requirement.startswith(dependency) for requirement in dependencies
        ), f"runtime dependencies must exclude {dependency}: {dependencies}"


def test_generation_is_strict_and_does_not_insert_files() -> None:
    """Fail on malformed sources without including build-host files or dates."""
    config = configparser.ConfigParser()
    config.read(_ROOT / "docs/man/docutils.conf", encoding="utf-8")
    general = config["general"]
    parsers = config["parsers"]
    assert general["halt-level"] == "warning", (
        f"docutils must halt on warnings: {general['halt-level']}"
    )
    assert general["exit-status-level"] == "warning", (
        f"docutils must fail on warnings: {general['exit-status-level']}"
    )
    assert not general["datestamp"], (
        f"manual pages must carry no build date: {general['datestamp']}"
    )
    assert not general.getboolean("generator"), "generator metadata must stay disabled"
    assert not parsers.getboolean("file-insertion-enabled"), (
        "file insertion must stay disabled so builds cannot read host files"
    )
    assert not parsers.getboolean("raw-enabled"), (
        "raw content must stay disabled so manuals cannot embed arbitrary roff"
    )


@pytest.mark.parametrize(
    "command",
    ["git-donkey", "git-track", "git-fafo", "git-plonk", "git-donkey-template"],
)
def test_manual_has_standard_sections(command: str) -> None:
    """Keep each generated page useful as a standalone reference."""
    source = (_ROOT / f"docs/man/{command}.rst").read_text(encoding="utf-8")
    lines = source.splitlines()
    assert source.startswith(f"{command}\n{'=' * len(command)}\n"), (
        f"{command}.rst must open with its title and underline: {lines[:2]}"
    )
    assert len(lines[3]) >= len(lines[2]), (
        f"{command}.rst subtitle underline must cover its title: {lines[2:4]}"
    )
    assert ":Manual section: 1" in source, (
        f"{command}.rst must declare ':Manual section: 1'"
    )
    for heading in ("SYNOPSIS", "DESCRIPTION", "OPTIONS", "EXAMPLES", "SEE ALSO"):
        assert f"\n{heading}\n" in source, f"{command}.rst must document {heading}"
    assert "--help" in source, f"{command}.rst must document --help"
    assert "--version" in source, f"{command}.rst must document --version"


@pytest.mark.parametrize(
    ("command", "required_text"),
    [
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
    ],
)
def test_manual_covers_command_specific_behaviour(
    command: str,
    required_text: str,
) -> None:
    """Preserve important options and configuration in the installed reference."""
    source = (_ROOT / f"docs/man/{command}.rst").read_text(encoding="utf-8")
    assert required_text in source, f"{command}.rst must document {required_text}"


@pytest.mark.parametrize(
    ("command", "wrapper"),
    [
        ("git-donkey", "_donkey_cli"),
        ("git-track", "_track_cli"),
        ("git-fafo", "_fafo_cli"),
        ("git-plonk", "_plonk_cli"),
        ("git-donkey-template", "_template_cli"),
    ],
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
        name = argument.arg.upper()
        assert name in source, f"{command}.rst must document the {name} argument"
    for argument in function.args.kwonlyargs:
        option = "--" + argument.arg.replace("_", "-")
        assert option in source, f"{command}.rst must document {option}"
