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
from tempfile import TemporaryDirectory

import hypothesis as hyp
import hypothesis.strategies as st
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
_SKYLOS_CLI_TOKENS: typ.Final = (
    "$(UV_ENV)",
    "uv",
    "tool",
    "run",
    "--python",
    "3.14",
    "--from",
    "skylos==$(SKYLOS_VERSION)",
    "skylos",
)
_SKYLOS_SCAN_TOKENS: typ.Final = (
    "$(SKYLOS_CLI)",
    "--config-file",
    "pyproject.toml",
)
_SKYLOS_LINT_TOKENS: typ.Final = (
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
_SKYLOS_WHITELIST_TOKENS: typ.Final = (
    "flock",
    "$(SKYLOS_WHITELIST_LOCK)",
    "$(SKYLOS_CLI)",
    "whitelist",
    "$${SKYLOS_SYMBOL}",
    "--reason",
    "$${SKYLOS_REASON}",
)
_SKYLOS_LINT_COMMAND_PREFIX: typ.Final = ("$(SKYLOS)",)
_SKYLOS_WHITELIST_COMMAND_PREFIX: typ.Final = ("flock",)
_SKYLOS_VERSION_TOKENS: typ.Final = ("4.33.2",)
_SKYLOS_PRODUCTION_TARGET_TOKENS: typ.Final = ("git_donkey",)
_SKYLOS_EXCLUDE_FOLDER_TOKENS: typ.Final = ("tests",)
_SKYLOS_WHITELIST_LOCK: typ.Final = ".skylos-whitelist.lock"
_SKYLOS_WHITELIST_LOCK_TOKENS: typ.Final = (_SKYLOS_WHITELIST_LOCK,)
_TEST_PREREQUISITES: typ.Final = ("build", "uv", "$(VENV_TOOLS)", "makeutil")
_FULL_SUITE_WORKFLOW_JOBS: typ.Final = frozenset((
    (".github/workflows/ci.yml", "lint-test"),
))
_EXPECTED_SKYLOS_WHITELIST_NAMES: typ.Final = frozenset[str]()
_EXPECTED_SKYLOS_DOCUMENTED_WHITELIST_NAMES: typ.Final = frozenset[str]()
_EXPECTED_SKYLOS_ENTRYPOINT_NAMES: typ.Final = frozenset[str]()
_SHELL_ARGUMENT_TEXT: typ.Final = st.builds(
    lambda prefix, content, suffix: f"{prefix}{content}{suffix}",
    st.text(alphabet=" \t", max_size=4),
    st.text(
        alphabet=(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
            "_$;|&'\"()[]{}*?!\\`"
        ),
        min_size=1,
        max_size=40,
    ),
    st.text(alphabet=" \t", max_size=4),
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


def _rule_prerequisites(target: str) -> tuple[str, ...]:
    """Return parsed prerequisites for ``target``."""
    return _text_sequence(
        _sole_recipe_rule(target).get("prerequisites"),
        subject=f"{target} prerequisites",
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


def _step_invokes_full_suite(step: dict[str, object]) -> bool:
    """Return whether a workflow step invokes the full pytest suite."""
    uses = step.get("uses")
    run = step.get("run")
    return (isinstance(uses, str) and "generate-coverage" in uses) or (
        isinstance(run, str) and ("make test" in run or "pytest" in run)
    )


def _full_suite_workflow_jobs() -> frozenset[tuple[str, str]]:
    """Return every workflow job that invokes the repository's full pytest suite."""
    jobs_with_full_suites: set[tuple[str, str]] = set()
    workflow_directory = REPOSITORY_ROOT / ".github" / "workflows"
    for workflow_file in workflow_directory.glob("*.yml"):
        workflow = _mapping(
            yaml.safe_load(workflow_file.read_text()),
            subject=f"{workflow_file} workflow",
        )
        jobs = _mapping(workflow.get("jobs"), subject=f"{workflow_file} jobs")
        for job_name, job in jobs.items():
            if not isinstance(job_name, str):
                continue
            job_mapping = _mapping(job, subject=f"{workflow_file} job {job_name!r}")
            raw_steps = job_mapping.get("steps")
            if not isinstance(raw_steps, list):
                continue
            steps = _objects(
                raw_steps, subject=f"{workflow_file} job {job_name!r} steps"
            )
            invokes_full_suite = any(_step_invokes_full_suite(step) for step in steps)
            if invokes_full_suite:
                jobs_with_full_suites.add((
                    str(workflow_file.relative_to(REPOSITORY_ROOT)),
                    job_name,
                ))
    return frozenset(jobs_with_full_suites)


def _run_skylos_allow(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Run a non-mutating whitelist boundary with a WSL-style ``NAME`` value."""
    environment = _skylos_allow_environment(*arguments)
    return subprocess.run(  # noqa: S603 - fixed Make target and arguments.
        _make_command("skylos-allow"),
        capture_output=True,
        check=False,
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
    )


def _skylos_allow_environment(*assignments: str) -> dict[str, str]:
    """Return a clean whitelist environment with a WSL-style ``NAME`` value."""
    environment: dict[str, str] = dict(os.environ)
    environment["NAME"] = "wsl-hostname"
    environment.pop("REASON", None)
    environment.pop("SYMBOL", None)
    for assignment in assignments:
        name, value = assignment.split("=", maxsplit=1)
        environment[name] = value
    return environment


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
    assert _variable_tokens("SKYLOS_VERSION") == _SKYLOS_VERSION_TOKENS, (
        "Skylos version contract must pin 4.33.2"
    )
    assert (
        _variable_tokens("SKYLOS_PRODUCTION_TARGETS")
        == _SKYLOS_PRODUCTION_TARGET_TOKENS
    ), "Skylos production-target contract must scan git_donkey"
    assert (
        _variable_tokens("SKYLOS_EXCLUDE_FOLDERS") == _SKYLOS_EXCLUDE_FOLDER_TOKENS
    ), "Skylos exclusion contract must omit tests"
    skylos_commands = [
        command
        for command in _recipe_tokens("lint")
        if command[:1] == _SKYLOS_LINT_COMMAND_PREFIX
    ]

    assert skylos_commands == [_SKYLOS_LINT_TOKENS], (
        "Skylos lint command must strictly scan only production dead code"
    )


def test_whitelist_target_uses_the_command_only_skylos_cli() -> None:
    """``skylos whitelist`` must precede its symbol and scan-only options."""
    assert _variable_tokens("SKYLOS_CLI") == _SKYLOS_CLI_TOKENS, (
        "Skylos CLI must pin Python 3.14 and the configured Skylos release"
    )
    assert _variable_tokens("SKYLOS") == _SKYLOS_SCAN_TOKENS, (
        "Skylos scan macro must add only the scan configuration file"
    )
    assert _variable_tokens("SKYLOS_WHITELIST_LOCK") == _SKYLOS_WHITELIST_LOCK_TOKENS, (
        "Skylos whitelist lock contract must use the ignored local lock file"
    )

    whitelist_commands = [
        command
        for command in _recipe_tokens("skylos-allow")
        if command[:1] == _SKYLOS_WHITELIST_COMMAND_PREFIX
    ]
    assert whitelist_commands == [_SKYLOS_WHITELIST_TOKENS], (
        "Skylos whitelist command must lock and dispatch before its reason option"
    )


def test_test_target_requires_the_makefile_parser() -> None:
    """``make test`` must verify Makeutil before contract tests run."""
    assert _rule_prerequisites("test") == _TEST_PREREQUISITES, (
        "test target must require the pinned Makefile parser alongside its "
        "existing build and virtual-environment prerequisites"
    )


def test_whitelist_lock_is_ignored() -> None:
    """The local whitelist lock must not be committed as repository state."""
    ignored_paths = frozenset((REPOSITORY_ROOT / ".gitignore").read_text().splitlines())
    assert _SKYLOS_WHITELIST_LOCK in ignored_paths, (
        "Skylos whitelist lock contract must ignore the local flock path"
    )


@hyp.settings(max_examples=25, deadline=None)
@hyp.given(value=st.text(alphabet=" \t", min_size=1, max_size=8))
def test_skylos_allow_rejects_missing_or_whitespace_values(value: str) -> None:
    """The whitelist target must reject missing and whitespace-only values."""
    for arguments, argument_name in (
        ((), "SYMBOL"),
        (("SYMBOL=handler",), "REASON"),
        ((f"SYMBOL={value}", "REASON=runtime caller"), "SYMBOL"),
        (("SYMBOL=handler", f"REASON={value}"), "REASON"),
    ):
        completed = _run_skylos_allow(*arguments)

        assert completed.returncode == 2, (
            f"Skylos whitelist boundary must return exit 2 when {argument_name} "
            "is missing or whitespace-only"
        )
        assert (
            f"Error: {argument_name} is required for a named whitelist exception"
            in completed.stderr
        ), (
            f"Skylos whitelist boundary must identify missing or whitespace-only "
            f"{argument_name}"
        )


@hyp.settings(max_examples=25, deadline=None)
@hyp.example(symbol=" $(handler);* ", reason=' Loaded "$plugin" | registry ')
@hyp.given(symbol=_SHELL_ARGUMENT_TEXT, reason=_SHELL_ARGUMENT_TEXT)
def test_skylos_allow_forwards_arguments_without_mutating_configuration(
    symbol: str, reason: str
) -> None:
    """The helper must forward exact environment values without configuration edits."""
    pyproject_path = REPOSITORY_ROOT / "pyproject.toml"
    original_pyproject = pyproject_path.read_bytes()

    with TemporaryDirectory() as temporary_directory:
        temporary_path = Path(temporary_directory)
        recorder_path = temporary_path / "skylos-recorder"
        arguments_path = temporary_path / "arguments.json"
        recorder_path.write_text(
            """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

Path(os.environ[\"SKYLOS_ARGUMENTS_PATH\"]).write_text(
    json.dumps(sys.argv[1:]), encoding=\"utf-8\"
)
""",
            encoding="utf-8",
        )
        recorder_path.chmod(0o755)
        environment = _skylos_allow_environment(f"SYMBOL={symbol}", f"REASON={reason}")
        environment["SKYLOS_ARGUMENTS_PATH"] = str(arguments_path)
        lock_path = temporary_path / "skylos-whitelist.lock"
        completed = subprocess.run(  # noqa: S603 - fixed Make target and recorder.
            _make_command(
                "--no-print-directory",
                "-f",
                str(REPOSITORY_ROOT / "Makefile"),
                f"SKYLOS_CLI={recorder_path}",
                f"SKYLOS_WHITELIST_LOCK={lock_path}",
                "skylos-allow",
            ),
            capture_output=True,
            check=False,
            cwd=temporary_path,
            env=environment,
            text=True,
        )

        assert completed.returncode == 0, (
            "Skylos whitelist boundary must forward complete SYMBOL and REASON "
            f"values through the recorder: {completed.stderr}"
        )
        assert json.loads(arguments_path.read_text(encoding="utf-8")) == [
            "whitelist",
            symbol,
            "--reason",
            reason,
        ], "Skylos whitelist boundary must preserve every argument exactly"

    assert pyproject_path.read_bytes() == original_pyproject, (
        "Skylos whitelist forwarding tests must not mutate pyproject.toml"
    )


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
    dead_code = _mapping(
        skylos.get("dead_code", {}), subject="Skylos dead-code configuration"
    )
    entrypoints = _objects(
        dead_code.get("entrypoints", []), subject="Skylos typed entry points"
    )
    entrypoint_names = frozenset(entrypoint.get("name") for entrypoint in entrypoints)

    assert gate.get("strict") is True, (
        "Skylos gate configuration must enable strict mode"
    )
    assert set(_text_sequence(whitelist.get("names"), subject="Skylos whitelist")) == (
        _EXPECTED_SKYLOS_WHITELIST_NAMES
    ), "Skylos whitelist names must match the consciously approved exception set"
    assert frozenset(documented) == _EXPECTED_SKYLOS_DOCUMENTED_WHITELIST_NAMES, (
        "Skylos documented whitelist must match the consciously approved exception set"
    )
    assert all(isinstance(name, str) for name in entrypoint_names), (
        "Skylos typed entry points must identify every approved symbol by name"
    )
    assert entrypoint_names == _EXPECTED_SKYLOS_ENTRYPOINT_NAMES, (
        "Skylos typed entry points must match the consciously approved runtime "
        "caller set"
    )


def test_full_suite_ci_jobs_install_the_pinned_makefile_parser() -> None:
    """Every isolated full-suite job must install Makeutil independently."""
    full_suite_jobs = _full_suite_workflow_jobs()
    assert full_suite_jobs == _FULL_SUITE_WORKFLOW_JOBS, (
        "full-suite CI contract must enumerate every workflow job that invokes "
        "pytest or coverage"
    )

    for workflow_path, job_name in full_suite_jobs:
        coverage_job = _workflow_job(workflow_path, job_name)
        environment = _mapping(
            coverage_job.get("env"),
            subject=f"{workflow_path} {job_name} Makeutil environment",
        )
        parser_step = _sole_workflow_step(
            workflow_path, job_name, "Install Makefile parser"
        )

        assert environment.get("MAKEUTIL_REVISION") == _MAKEUTIL_REVISION, (
            f"{workflow_path} {job_name} Makeutil revision contract must stay pinned"
        )
        assert environment.get("MAKEUTIL_TOOLCHAIN") == _MAKEUTIL_TOOLCHAIN, (
            f"{workflow_path} {job_name} Makeutil toolchain contract must stay pinned"
        )
        _assert_makeutil_installation(
            parser_step.get("run"),
            contract=f"{workflow_path} {job_name} Makeutil-install contract",
        )
