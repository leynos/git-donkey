"""Shared fixtures for the unit-level contract tests.

The contract tests inspect the repository's Makefile and workflows from the
repository root and shell out to ``make``. This ``conftest`` provides those
shared entry points once, so individual contract modules do not repeat the
path derivation or executable discovery.
"""

from __future__ import annotations

import shutil
import typing as typ
from pathlib import Path

import pytest

# ``typing`` gates the annotation-only ``collections.abc`` import below: a
# module-level import used solely in annotations trips TC003, and the
# from-import form is banned by the repository's import conventions.
if typ.TYPE_CHECKING:
    import collections.abc as cabc


@pytest.fixture(scope="session")
def repository_root() -> Path:
    """Return the repository root that owns the contract tests.

    Returns
    -------
    Path
        Absolute path to the repository root.

    """
    return Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def make_executable() -> str:
    """Return the resolved ``make`` executable required by the contract tests.

    Returns
    -------
    str
        Absolute path to the ``make`` executable.

    """
    executable = shutil.which("make")
    if executable is None:
        pytest.fail("unit contract tests require the make executable on PATH")
    return executable


@pytest.fixture(scope="session")
def make_command(make_executable: str) -> cabc.Callable[..., tuple[str, ...]]:
    """Return a factory that prefixes arguments with the make executable.

    Parameters
    ----------
    make_executable : str
        Resolved path to the ``make`` executable.

    Returns
    -------
    collections.abc.Callable[..., tuple[str, ...]]
        Factory returning the executable followed by the given arguments.

    """

    def build(*arguments: str) -> tuple[str, ...]:
        """Return the make executable followed by ``arguments``.

        Parameters
        ----------
        *arguments : str
            Arguments passed to ``make``.

        Returns
        -------
        tuple[str, ...]
            The executable and its arguments as an argv sequence.

        """
        return (make_executable, *arguments)

    return build
