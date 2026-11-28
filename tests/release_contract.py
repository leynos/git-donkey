"""Readers and scanners shared by the release workflow contract tests.

The contract tests parse the checked-in workflows and composite actions rather
than executing them. This module owns document discovery and the executable
step scan, so the structural contracts and the ``run`` script scan read the
same declarations.
"""

from __future__ import annotations

import pathlib as pth
import re
import typing as typ

import yaml

if typ.TYPE_CHECKING:
    import collections.abc as cabc

PINNED = re.compile(r"@[0-9a-f]{40}$")
ROOT = pth.Path(__file__).resolve().parents[1]


def document_paths() -> list[pth.Path]:
    """Return every workflow and composite-action file the contracts scan."""
    return [
        *sorted((ROOT / ".github" / "workflows").glob("*.yml")),
        *sorted((ROOT / ".github" / "workflows").glob("*.yaml")),
        *sorted((ROOT / ".github" / "actions").glob("*/action.yml")),
        *sorted((ROOT / ".github" / "actions").glob("*/action.yaml")),
    ]


def document_name(path: pth.Path) -> str:
    """Name a workflow by file name and a composite action by its path."""
    if path.parent.name == "workflows":
        return path.name
    return path.relative_to(ROOT).as_posix()


def document(path: pth.Path) -> dict[str, typ.Any]:
    """Parse one workflow or composite action file."""
    return typ.cast(
        "dict[str, typ.Any]", yaml.safe_load(path.read_text(encoding="utf-8"))
    )


def ci_documents() -> dict[str, object]:
    """Parse every workflow and composite action in the repository."""
    return {document_name(path): document(path) for path in document_paths()}


def step_containers(
    parsed: dict[str, typ.Any],
) -> list[list[dict[str, typ.Any]]]:
    """Return the declared step lists of a workflow or composite action."""
    containers: list[list[dict[str, typ.Any]]] = []
    jobs = typ.cast("dict[str, typ.Any]", parsed.get("jobs", {}))
    for job in jobs.values():
        steps = typ.cast("dict[str, typ.Any]", job).get("steps")
        if isinstance(steps, list):
            containers.append([typ.cast("dict[str, typ.Any]", item) for item in steps])
    runs = typ.cast("dict[str, typ.Any]", parsed.get("runs", {}))
    action_steps = runs.get("steps")
    if isinstance(action_steps, list):
        containers.append([
            typ.cast("dict[str, typ.Any]", item) for item in action_steps
        ])
    return containers


def declared_steps(parsed: dict[str, typ.Any]) -> list[dict[str, typ.Any]]:
    """Return every step a workflow or composite action declares."""
    return [item for steps in step_containers(parsed) for item in steps]


def run_scripts(
    parsed: object, where: str
) -> cabc.Iterator[tuple[str, dict[str, typ.Any]]]:
    """Yield each executable step's ``run`` script, with its location."""
    if not isinstance(parsed, dict):
        return
    containers = step_containers(typ.cast("dict[str, typ.Any]", parsed))
    for container_index, steps in enumerate(containers):
        for index, item in enumerate(steps):
            if isinstance(item.get("run"), str):
                yield f"{where}.steps[{container_index}][{index}]", item


def expansions_in(
    name: str,
    parsed: object,
    allowed: cabc.Collection[tuple[str, object, str]],
) -> list[str]:
    """Return each ``run`` script location that expands ``${{`` beyond ``allowed``.

    Parameters
    ----------
    name : str
        Workflow file name or composite-action path.
    parsed : object
        The parsed workflow or action document.
    allowed : collections.abc.Collection[tuple[str, object, str]]
        Reviewed ``(name, step id, script)`` exceptions.

    Returns
    -------
    list[str]
        One location per script that pastes a template expression.
    """
    return [
        where
        for where, holder in run_scripts(parsed, name)
        if "${{" in holder["run"]
        and (name, holder.get("id"), holder["run"].strip()) not in allowed
    ]
