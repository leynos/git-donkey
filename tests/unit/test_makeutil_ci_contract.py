"""Contract tests for the Makefile parser and the CI jobs that provision it.

Makeutil supplies structured Makefile facts, avoiding fragile source-text
matching for the tool provisioning that the test and type-check targets rely
on, and the full-suite CI jobs are held to one install-and-verify shape.
"""

from __future__ import annotations

import functools
import json
import os
import re
import shlex
import subprocess  # ruff: ignore[suspicious-subprocess-import] - fixed commands exercise build boundaries.
import typing as typ
from pathlib import Path

import yaml

if typ.TYPE_CHECKING:
    import collections.abc as cabc

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_MAKEUTIL_COMMAND: typ.Final = ("makeutil", "parse", "Makefile")
_INSTALL_MAKEUTIL_ACTION: typ.Final = (
    "leynos/shared-actions/.github/actions/install-makeutil"
    "@ebe2f3105334283441f4c915eb979a0f4b9f2c04"
)
_TEST_PREREQUISITES: typ.Final = ("build", "uv", "$(VENV_TOOLS)", "makeutil")
_FULL_SUITE_WORKFLOW_JOBS: typ.Final = frozenset((
    (".github/workflows/ci.yml", "lint-test"),
    (".github/workflows/coverage-main.yml", "coverage-upload"),
))


@functools.cache
def _makefile_report() -> dict[str, object]:
    """Return the cached, complete Makeutil report for the test session."""
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed local parser command.
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


def _step_index(steps: list[dict[str, object]], step_name: str, *, subject: str) -> int:
    """Return the position of the sole named step in a workflow job."""
    matches = [
        index for index, step in enumerate(steps) if step.get("name") == step_name
    ]
    assert len(matches) == 1, (
        f"{subject} must have exactly one {step_name!r} step, found {len(matches)}"
    )
    return matches[0]


def _sole_workflow_step(
    workflow_path: str, job_name: str, step_name: str
) -> dict[str, object]:
    """Return the sole named step from a GitHub Actions job."""
    job = _workflow_job(workflow_path, job_name)
    steps = _objects(
        job.get("steps"), subject=f"{workflow_path} job {job_name!r} steps"
    )
    index = _step_index(steps, step_name, subject=f"{workflow_path} job {job_name!r}")
    return steps[index]


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


def _contract_full_suite_jobs() -> frozenset[tuple[str, str]]:
    """Return the full-suite jobs, asserting the enumerated CI contract."""
    full_suite_jobs = _full_suite_workflow_jobs()
    assert full_suite_jobs == _FULL_SUITE_WORKFLOW_JOBS, (
        "full-suite CI contract must enumerate every workflow job that invokes "
        "pytest or coverage"
    )
    return full_suite_jobs


def _assert_makeutil_installation(step: dict[str, object], *, contract: str) -> None:
    """Assert that ``step`` runs the pinned prebuilt install action, defaults only.

    A ``run`` key would mean a from-source install had crept back, and a
    ``with`` key would move the version off the action's own default and digest
    table.
    """
    assert step.get("uses") == _INSTALL_MAKEUTIL_ACTION, (
        f"{contract} must use the pinned install-makeutil action"
    )
    assert "run" not in step, f"{contract} must not also run an install command"
    assert "with" not in step, f"{contract} must take the action's default version"


def _assert_makeutil_verification(
    install_step: dict[str, object],
    verify_step: dict[str, object],
    *,
    contract: str,
) -> None:
    """Assert the smoke step that proves the installed binary is usable.

    The install step needs an id so the verify step can read the version the
    action reports; the verify step must compare the binary's own version with
    it and require a complete parse of the repository Makefile.
    """
    assert install_step.get("id") == "makeutil", f"{contract} install step needs id"
    environment = _mapping(verify_step.get("env"), subject=f"{contract} environment")
    assert environment.get("INSTALLED_VERSION") == (
        "${{ steps.makeutil.outputs.version }}"
    ), f"{contract} must read the version the install action reports"
    script = verify_step.get("run")
    assert isinstance(script, str), f"{contract} must run a verification script"
    assert 'test "$(makeutil --version)" = "makeutil ${INSTALLED_VERSION}"' in script, (
        f"{contract} must compare the binary's version with the installed one"
    )
    assert "makeutil parse Makefile" in script, (
        f"{contract} must parse the repository Makefile"
    )
    assert '["parse"]["status"] == "complete"' in script, (
        f"{contract} must require a complete parse"
    )
    assert not re.search(r"\d+\.\d+\.\d+", script), (
        f"{contract} must compare versions, never name one"
    )


def _assert_verification_follows_install(workflow_path: str, job_name: str) -> None:
    """Assert the verify step directly follows the install step."""
    steps = _objects(
        _workflow_job(workflow_path, job_name).get("steps"),
        subject=f"{workflow_path} {job_name} steps",
    )
    names = [step.get("name") for step in steps]
    position = names.index("Install makeutil")
    assert names[position + 1] == "Verify makeutil", (
        f"{workflow_path} {job_name} must verify makeutil right after installing it"
    )


def _assert_makeutil_install_precedes_full_suite(
    workflow_path: str, job_name: str
) -> None:
    """Assert a job installs Makeutil before the step that runs the full suite."""
    subject = f"{workflow_path} {job_name}"
    steps = _objects(
        _workflow_job(workflow_path, job_name).get("steps"), subject=f"{subject} steps"
    )
    install_index = _step_index(steps, "Install makeutil", subject=subject)
    suite_indexes = [
        index for index, step in enumerate(steps) if _step_invokes_full_suite(step)
    ]

    assert suite_indexes, f"{subject} must invoke the full suite from a step"
    assert install_index < min(suite_indexes), (
        f"{subject} must install Makeutil before the full suite so its contract "
        "tests can parse the Makefile"
    )


def test_test_target_requires_the_makefile_parser() -> None:
    """``make test`` must verify Makeutil before contract tests run."""
    assert _rule_prerequisites("test") == _TEST_PREREQUISITES, (
        "test target must require the pinned Makefile parser alongside its "
        "existing build and virtual-environment prerequisites"
    )


def test_makeutil_target_requires_the_parser_on_path(
    make_command: cabc.Callable[..., tuple[str, ...]], tmp_path: Path
) -> None:
    """``make makeutil`` must report a missing parser instead of passing silently."""
    environment = dict(os.environ)
    environment["PATH"] = str(tmp_path)
    environment["SHELL"] = "/bin/sh"

    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed Make target without a shell.
        make_command("--no-print-directory", "makeutil"),
        capture_output=True,
        check=False,
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
    )

    assert completed.returncode != 0, (
        "make makeutil must fail when the parser is absent from PATH: "
        f"{completed.stdout}"
    )
    assert "Error: 'makeutil' is required, but not installed" in completed.stderr, (
        f"make makeutil must report the parser it requires: {completed.stderr}"
    )


def test_typecheck_provisions_ty_from_the_pinned_tool_run() -> None:
    """Ty must come from the pinned tool run, never from an installed tool."""
    tools = {token.lower() for token in _variable_tokens("TOOLS")}
    typecheck_prerequisites = {
        token.lower() for token in _rule_prerequisites("typecheck")
    }

    assert not {"ty", "$(ty)"} & tools, (
        "TOOLS must not require a globally installed ty; the typecheck target "
        "pins its release through TY_VERSION"
    )
    assert not {"ty", "$(ty)"} & typecheck_prerequisites, (
        "typecheck must not depend on an installed ty prerequisite; the target "
        "verifies the pinned release itself"
    )


def test_full_suite_ci_jobs_install_the_pinned_makefile_parser() -> None:
    """Every isolated full-suite job must install Makeutil independently."""
    for workflow_path, job_name in _contract_full_suite_jobs():
        subject = f"{workflow_path} {job_name}"
        job = _workflow_job(workflow_path, job_name)
        environment = _mapping(job.get("env", {}), subject=f"{subject} environment")
        steps = _objects(job.get("steps"), subject=f"{subject} steps")

        assert "MAKEUTIL_REVISION" not in environment, (
            f"{subject} must not carry a from-source Makeutil pin"
        )
        assert "MAKEUTIL_TOOLCHAIN" not in environment, (
            f"{subject} must not carry a Makeutil nightly toolchain"
        )
        assert not any(step.get("name") == "Cache Makefile parser" for step in steps), (
            f"{subject} must leave Makeutil caching to the install action"
        )
        _assert_makeutil_installation(
            _sole_workflow_step(workflow_path, job_name, "Install makeutil"),
            contract=f"{subject} Makeutil-install contract",
        )
        _assert_makeutil_verification(
            _sole_workflow_step(workflow_path, job_name, "Install makeutil"),
            _sole_workflow_step(workflow_path, job_name, "Verify makeutil"),
            contract=f"{subject} Makeutil-verify contract",
        )
        _assert_verification_follows_install(workflow_path, job_name)
        _assert_makeutil_install_precedes_full_suite(workflow_path, job_name)


def test_full_suite_ci_jobs_grant_only_contents_read() -> None:
    """Every isolated full-suite job must keep its workflow token read-only."""
    for workflow_path, job_name in _contract_full_suite_jobs():
        permissions = _mapping(
            _workflow_job(workflow_path, job_name).get("permissions"),
            subject=f"{workflow_path} {job_name} permissions",
        )

        assert permissions == {"contents": "read"}, (
            f"{workflow_path} {job_name} must grant only contents: read so its "
            "workflow token cannot write to the repository"
        )
