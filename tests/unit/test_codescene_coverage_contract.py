"""Contract-test main-owned CodeScene coverage publication."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"
MAIN_PATH = ROOT / ".github" / "workflows" / "coverage-main.yml"
SHARED_ACTION_REVISION = "152d9c4784d0ae5877938a984fe6d1f04d718fd8"
GENERATOR = (
    f"leynos/shared-actions/.github/actions/generate-coverage@{SHARED_ACTION_REVISION}"
)
UPLOADER = (
    "leynos/shared-actions/.github/actions/upload-codescene-coverage@"
    f"{SHARED_ACTION_REVISION}"
)
GENERATOR_INPUTS = {
    "language": "python",
    "python-source": "./git_donkey",
    "output-path": "coverage.xml",
    "format": "cobertura",
    "pytest-workers": "",
    "with-ratchet": "true",
}


def _workflow(path: Path) -> dict[str, object]:
    """Load one workflow as a mapping."""
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{path.name} must be a YAML mapping"
    return typ.cast("dict[str, object]", loaded)


def _mapping(value: object, subject: str) -> dict[str, object]:
    """Return a mapping while identifying an unexpected workflow value."""
    assert isinstance(value, dict), f"{subject} must be a mapping"
    return typ.cast("dict[str, object]", value)


def _triggers(workflow: dict[str, object]) -> dict[str, object]:
    """Return workflow triggers despite PyYAML's YAML 1.1 ``on`` parsing."""
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict), "workflow must declare an on mapping"
    return typ.cast("dict[str, object]", triggers)


def _job(workflow: dict[str, object], name: str) -> dict[str, object]:
    """Return a named workflow job."""
    jobs = _mapping(workflow.get("jobs"), "workflow jobs")
    return _mapping(jobs.get(name), f"workflow job {name!r}")


def _step(job: dict[str, object], name: str) -> dict[str, object]:
    """Return the sole named job step."""
    steps = job.get("steps")
    assert isinstance(steps, list), "job must declare steps"
    matches = [
        step for step in steps if isinstance(step, dict) and step.get("name") == name
    ]
    assert len(matches) == 1, f"job must declare one {name!r} step"
    return typ.cast("dict[str, object]", matches[0])


def test_pull_request_coverage_is_local_and_ratcheted() -> None:
    """Keep the pull-request coverage boundary independent of CodeScene."""
    workflow = _workflow(CI_PATH)
    assert "pull_request" in _triggers(workflow), "CI must run for pull requests"
    lint_test = _job(workflow, "lint-test")
    coverage = _step(lint_test, "Run tests with coverage")

    assert coverage.get("if") == "github.event_name == 'pull_request'", (
        "coverage must run only for pull requests"
    )
    assert coverage.get("uses") == GENERATOR, "coverage must use the shared generator"
    assert coverage.get("with") == GENERATOR_INPUTS, (
        "coverage must stay serial, source-scoped and ratcheted"
    )
    assert "CS_ACCESS_TOKEN" not in _mapping(workflow.get("env", {}), "workflow env"), (
        "pull-request workflow must not expose the CodeScene token"
    )
    assert "CS_ACCESS_TOKEN" not in _mapping(
        lint_test.get("env", {}), "lint-test env"
    ), "pull-request job must not expose the CodeScene token"

    steps = lint_test.get("steps")
    assert isinstance(steps, list), "lint-test must declare steps"
    assert all(
        "upload-codescene-coverage" not in str(step) and "cs-coverage" not in str(step)
        for step in steps
    ), "pull-request CI must not invoke CodeScene"

    checkout = _step(lint_test, "Check out repository")
    checkout_inputs = checkout.get("with") or {}
    assert isinstance(checkout_inputs, dict), "checkout inputs must be a mapping"
    assert checkout_inputs.get("fetch-depth") != 0, (
        "pull-request coverage must not require full checkout history"
    )


def test_main_coverage_advances_the_ratchet_and_publishes() -> None:
    """Require main pushes to generate and explicitly upload coverage."""
    workflow = _workflow(MAIN_PATH)
    assert _triggers(workflow) == {"push": {"branches": ["main"]}}, (
        "coverage publication must be restricted to pushes to main"
    )
    coverage_upload = _job(workflow, "coverage-upload")
    coverage = _step(coverage_upload, "Generate coverage")
    upload = _step(coverage_upload, "Upload coverage data to CodeScene")

    assert coverage.get("uses") == GENERATOR, "main must use the shared generator"
    assert coverage.get("with") == GENERATOR_INPUTS, (
        "main must generate the same ratcheted coverage as pull requests"
    )
    assert upload.get("uses") == UPLOADER, "main must use the shared uploader"
    inputs = upload.get("with")
    assert isinstance(inputs, dict), "CodeScene upload must declare inputs"
    assert inputs.get("mode") == "upload", "main must upload coverage data"
