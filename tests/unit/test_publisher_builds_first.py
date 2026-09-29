"""The publisher builds the project before it measures coverage.

The manpage packaging tests build a wheel offline against the ``.uv-cache`` that
only ``make build`` fills, so without that step every main run fails in coverage
and never uploads, as run 36028370138 did. This is a repository fact, not a
CV-005 rule, so it stays here when the shared rules move to ``cv005-contracts``.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import yaml

PUBLISHER: typ.Final[Path] = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "coverage-main.yml"
)
COVERAGE_ACTION: typ.Final[str] = "/.github/actions/generate-coverage@"


def _steps_of_the_coverage_job() -> list[dict[str, object]]:
    """Return the steps of the publisher job that generates coverage."""
    jobs = yaml.safe_load(PUBLISHER.read_text(encoding="utf-8"))["jobs"]
    for job in jobs.values():
        steps = job.get("steps", [])
        if any(COVERAGE_ACTION in str(step.get("uses")) for step in steps):
            return typ.cast("list[dict[str, object]]", steps)
    message = "no publisher job runs generate-coverage"
    raise AssertionError(message)


def test_the_publisher_runs_make_build_before_it_measures() -> None:
    """``make build`` runs, unconditionally, ahead of the coverage step."""
    steps = _steps_of_the_coverage_job()
    coverage = next(
        index
        for index, step in enumerate(steps)
        if COVERAGE_ACTION in str(step.get("uses"))
    )
    builds = [
        step
        for step in steps[:coverage]
        if str(step.get("run", "")).strip() == "make build" and "if" not in step
    ]
    assert builds, "the coverage job must run `make build` before coverage"
