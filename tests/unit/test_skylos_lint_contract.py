"""Contract tests for Skylos dead-code detection in Make and CI.

Skylos's scanner accepts ``--config-file`` before a scan path, whereas the
standalone ``whitelist`` subcommand must immediately follow ``skylos``. Skylos
also uses its own runtime AST, so Python 3.14 is part of the command contract.
Makeutil supplies structured Makefile facts, avoiding fragile source-text
matching for the command order and production-gate behaviour.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess  # noqa: S404 - fixed commands exercise build boundaries.
import tomllib
import typing as typ
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_MAKEUTIL_COMMAND: typ.Final = ("makeutil", "parse", "Makefile")
_MAKEUTIL_REVISION: typ.Final = "29fc5a1634ffbaa18a773eed9dff1b2838a45d9c"
_MAKEUTIL_TOOLCHAIN: typ.Final = "nightly-2026-05-28"
_MAKEUTIL_INSTALL_TOKENS: typ.Final = (
    "rustup",
    "toolchain",
    "install",
    "${MAKEUTIL_TOOLCHAIN}",
    "--profile",
    "minimal",
    "RUSTFLAGS=-Zpolonius=next",
    "cargo",
    "+${MAKEUTIL_TOOLCHAIN}",
    "install",
    "--git",
    "https://github.com/leynos/makeutil",
    "--rev",
    "${MAKEUTIL_REVISION}",
    "--locked",
    "--force",
    "makeutil",
)


def _makefile_report() -> dict[str, object]:
    """Return a newly parsed, complete Makeutil report."""
    completed = subprocess.run(  # noqa: S603 - fixed local parser command.
        _MAKEUTIL_COMMAND,
        capture_output=True,
        check=True,
        cwd=REPOSITORY_ROOT,
        text=True,
    )
    report = typ.cast("dict[str, object]", json.loads(completed.stdout))
    parse = _mapping(report.get("parse"), subject="Makeutil parse report")
    assert parse.get("status") == "complete", (
        f"Makeutil must complete the Makefile parse, got {parse!r}"
    )
    return report


def _mapping(value: object, *, subject: str) -> dict[str, object]:
    """Return a JSON object, identifying an unexpected subject."""
    assert isinstance(value, dict), f"{subject} must be a JSON object"
    return typ.cast("dict[str, object]", value)


def _objects(value: object, *, subject: str) -> list[dict[str, object]]:
    """Return a JSON object array, identifying an unexpected subject."""
    assert isinstance(value, list), f"{subject} must be a JSON array"
    return [_mapping(item, subject=f"{subject} item") for item in value]


def _text_sequence(value: object, *, subject: str) -> tuple[str, ...]:
    """Return a JSON string array, identifying an unexpected subject."""
    assert isinstance(value, list), f"{subject} must be a JSON array"
    assert all(isinstance(item, str) for item in value), (
        f"{subject} must contain only JSON strings"
    )
    return tuple(typ.cast("list[str]", value))


def _sole_variable(name: str) -> dict[str, object]:
    """Return Makeutil's sole variable fact for ``name``."""
    variables = _objects(_makefile_report().get("variables"), subject="variables")
    matches = [variable for variable in variables if variable.get("name") == name]
    assert len(matches) == 1, (
        f"Makeutil must report exactly one {name!r} variable, found {len(matches)}"
    )
    return matches[0]


def _sole_recipe_rule(target: str) -> dict[str, object]:
    """Return the one parsed rule for ``target`` with recipes."""
    rules = _objects(_makefile_report().get("rules"), subject="rules")
    matches = [
        rule
        for rule in rules
        if target in _text_sequence(rule.get("targets"), subject="rule targets")
        and _objects(rule.get("recipes"), subject="rule recipes")
    ]
    assert len(matches) == 1, (
        f"Makeutil must report exactly one recipe-bearing {target!r} rule, "
        f"found {len(matches)}"
    )
    return matches[0]


def _variable_tokens(name: str) -> tuple[str, ...]:
    """Return shell-like tokens from a Makefile variable's raw value."""
    value = _sole_variable(name).get("raw_value")
    assert isinstance(value, str), f"{name!r} must have a string Makefile value"
    return tuple(shlex.split(value))


def _recipe_tokens(target: str) -> tuple[tuple[str, ...], ...]:
    """Return shell-like tokens for every recipe in ``target``."""
    recipes = _objects(
        _sole_recipe_rule(target).get("recipes"), subject=f"{target} recipes"
    )
    return tuple(
        tuple(shlex.split(recipe_text.replace("\\\n", "")))
        for recipe in recipes
        if isinstance(recipe_text := recipe.get("text"), str)
    )


def _workflow_job(workflow_path: str, job_name: str) -> dict[str, object]:
    """Return a named GitHub Actions job."""
    workflow = yaml.safe_load((REPOSITORY_ROOT / workflow_path).read_text())
    workflow_mapping = _mapping(workflow, subject=f"{workflow_path} workflow")
    jobs = _mapping(workflow_mapping.get("jobs"), subject=f"{workflow_path} jobs")
    return _mapping(jobs.get(job_name), subject=f"{workflow_path} job {job_name!r}")


def _sole_workflow_step(
    workflow_path: str, job_name: str, step_name: str
) -> dict[str, object]:
    """Return the sole named step from a GitHub Actions job."""
    job = _workflow_job(workflow_path, job_name)
    steps = _objects(
        job.get("steps"), subject=f"{workflow_path} job {job_name!r} steps"
    )
    matches = [step for step in steps if step.get("name") == step_name]
    assert len(matches) == 1, (
        f"{workflow_path} job {job_name!r} must have exactly one "
        f"{step_name!r} step, found {len(matches)}"
    )
    return matches[0]


def _run_skylos_allow(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Run a non-mutating whitelist boundary with a WSL-style ``NAME`` value."""
    environment: dict[str, str] = dict(os.environ)
    environment["NAME"] = "wsl-hostname"
    environment.pop("REASON", None)
    environment.pop("SYMBOL", None)
    return subprocess.run(  # noqa: S603 - fixed Make target and arguments.
        _make_command("skylos-allow", *arguments),
        capture_output=True,
        check=False,
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
    )


def _make_command(*arguments: str) -> tuple[str, ...]:
    """Return the resolved Make executable followed by ``arguments``."""
    make_executable = shutil.which("make")
    assert make_executable is not None, (
        "Skylos Makefile boundary tests require the make executable"
    )
    return make_executable, *arguments


def _assert_makeutil_installation(command: object, *, contract: str) -> None:
    """Assert that ``command`` installs the pinned Makeutil parser."""
    assert isinstance(command, str), (
        f"{contract} must provide a Makeutil installation shell command"
    )
    assert (
        tuple(shlex.split(command.replace("\\\n", ""))) == _MAKEUTIL_INSTALL_TOKENS
    ), f"{contract} must install the pinned Makeutil revision with Polonius"


def test_lint_recipe_runs_the_production_dead_code_gate() -> None:
    """``make lint`` must run the strict production-only Skylos scan."""
    assert _variable_tokens("SKYLOS_VERSION") == ("4.33.2",), (
        "Skylos version contract must pin 4.33.2"
    )
    assert _variable_tokens("SKYLOS_PRODUCTION_TARGETS") == ("git_donkey",), (
        "Skylos production-target contract must scan git_donkey"
    )
    assert _variable_tokens("SKYLOS_EXCLUDE_FOLDERS") == ("tests",), (
        "Skylos exclusion contract must omit tests"
    )
    skylos_commands = [
        command for command in _recipe_tokens("lint") if command[:1] == ("$(SKYLOS)",)
    ]

    assert skylos_commands == [
        (
            "$(SKYLOS)",
            "$(SKYLOS_PRODUCTION_TARGETS)",
            "--exclude",
            "$(SKYLOS_EXCLUDE_FOLDERS)",
            "--category",
            "dead_code",
            "--gate",
            "--format",
            "concise",
            "--no-upload",
            "--no-provenance",
            "--no-grep-verify",
        )
    ], "Skylos lint command must strictly scan only production dead code"


def test_whitelist_target_uses_the_command_only_skylos_cli() -> None:
    """``skylos whitelist`` must precede its symbol and scan-only options."""
    assert _variable_tokens("SKYLOS_CLI") == (
        "$(UV_ENV)",
        "uv",
        "tool",
        "run",
        "--python",
        "3.14",
        "--from",
        "skylos==$(SKYLOS_VERSION)",
        "skylos",
    ), "Skylos CLI must pin Python 3.14 and the configured Skylos release"
    assert _variable_tokens("SKYLOS") == (
        "$(SKYLOS_CLI)",
        "--config-file",
        "pyproject.toml",
    ), "Skylos scan macro must add only the scan configuration file"

    whitelist_commands = [
        command
        for command in _recipe_tokens("skylos-allow")
        if command[:1] == ("$(SKYLOS_CLI)",)
    ]
    assert whitelist_commands == [
        (
            "$(SKYLOS_CLI)",
            "whitelist",
            "$${SKYLOS_SYMBOL}",
            "--reason",
            "$${SKYLOS_REASON}",
        )
    ], "Skylos whitelist command must dispatch before its reason option"


def test_skylos_allow_requires_symbol_and_reason() -> None:
    """The whitelist target must reject incomplete input before running Skylos."""
    for arguments, expected_error, argument_name in (
        ((), "Error: SYMBOL is required for a named whitelist exception", "SYMBOL"),
        (
            ("SYMBOL=handler",),
            "Error: REASON is required for a named whitelist exception",
            "REASON",
        ),
    ):
        completed = _run_skylos_allow(*arguments)

        assert completed.returncode == 2, (
            f"Skylos whitelist boundary must return exit 2 when {argument_name} "
            "is missing"
        )
        assert expected_error in completed.stderr, (
            f"Skylos whitelist boundary must identify missing {argument_name}"
        )


def test_skylos_allow_dry_run_preserves_whitelist_argument_order() -> None:
    """A complete dry run must expose the non-mutating whitelist command."""
    completed = subprocess.run(  # noqa: S603 - fixed Make target and arguments.
        _make_command(
            "--dry-run",
            "skylos-allow",
            "SYMBOL=handler",
            "REASON=Loaded by plugin registry",
        ),
        capture_output=True,
        check=False,
        cwd=REPOSITORY_ROOT,
        text=True,
    )

    assert completed.returncode == 0, (
        "Skylos whitelist dry run must accept complete SYMBOL and REASON input"
    )
    assert (
        'skylos whitelist "${SKYLOS_SYMBOL}" --reason "${SKYLOS_REASON}"'
        in completed.stdout
    ), "Skylos whitelist dry run must preserve subcommand-first argument order"


def test_skylos_configuration_is_strict_with_no_unverified_exceptions() -> None:
    """Skylos must remain strict until an explained false positive is verified."""
    with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as configuration_file:
        configuration = tomllib.load(configuration_file)

    tool = _mapping(configuration.get("tool"), subject="tool configuration")
    skylos = _mapping(tool.get("skylos"), subject="Skylos configuration")
    gate = _mapping(skylos.get("gate"), subject="Skylos gate configuration")
    whitelist = _mapping(
        skylos.get("whitelist"), subject="Skylos whitelist configuration"
    )
    documented = _mapping(
        whitelist.get("documented"), subject="documented Skylos whitelist"
    )

    assert gate.get("strict") is True, (
        "Skylos gate configuration must enable strict mode"
    )
    assert whitelist.get("names") == [], (
        "Skylos whitelist must remain empty without verified false positives"
    )
    assert documented == {}, (
        "Skylos documented whitelist must remain empty without verified false positives"
    )


def test_coverage_ci_installs_the_pinned_makefile_parser() -> None:
    """The isolated full-suite coverage job must install Makeutil independently."""
    workflow_path = ".github/workflows/ci.yml"
    job_name = "lint-test"
    coverage_job = _workflow_job(workflow_path, job_name)
    environment = _mapping(
        coverage_job.get("env"), subject=f"{workflow_path} Makeutil environment"
    )
    coverage_step = _sole_workflow_step(
        workflow_path, job_name, "Run tests with coverage"
    )
    parser_step = _sole_workflow_step(
        workflow_path, job_name, "Install Makefile parser"
    )

    assert environment.get("MAKEUTIL_REVISION") == _MAKEUTIL_REVISION, (
        "coverage Makeutil revision contract must stay pinned"
    )
    assert environment.get("MAKEUTIL_TOOLCHAIN") == _MAKEUTIL_TOOLCHAIN, (
        "coverage Makeutil toolchain contract must stay pinned"
    )
    assert isinstance(coverage_step.get("uses"), str), (
        "coverage job contract must run its full pytest suite through an action"
    )
    _assert_makeutil_installation(
        parser_step.get("run"), contract="coverage Makeutil-install contract"
    )
