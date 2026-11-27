#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Check that a build produced a complete source distribution and wheel.

The release publishes exactly one source distribution and the wheel built from
it, and a wheel rebuilt from the sdist must contain every manual page. A
missing or incomplete sdist would otherwise surface only at tag time, so the
same build runs on every pull request: exactly one sdist and at least one wheel
must exist, the sdist must carry the manual-page sources and Docutils
configuration, and every wheel must carry the generated pages for each console
script declared in ``pyproject.toml``.

Examples
--------
>>> main(["--dist", "dist"])
0
"""

from __future__ import annotations

import argparse
import sys
import tarfile
import tomllib
import typing as typ
import zipfile
from pathlib import Path

_ERROR = "::error title=check-dist::"
_SDIST_SUFFIX = ".tar.gz"
_WHEEL_SUFFIX = ".whl"
_MAN_SOURCE_DIR = "docs/man"
_MAN_DATA_SUFFIX = ".data/data/share/man/man1/"


def console_scripts(root: Path) -> tuple[str, ...]:
    """Return the console-script names declared in ``pyproject.toml``.

    Parameters
    ----------
    root : pathlib.Path
        Repository root holding ``pyproject.toml``.

    Returns
    -------
    tuple[str, ...]
        The sorted console-script names.

    Examples
    --------
    >>> console_scripts(Path.cwd())[:1]
    ('git-donkey',)
    """
    document = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return tuple(sorted(document["project"]["scripts"]))


def _archives(directory: Path, suffix: str) -> list[Path]:
    """Return the distribution archives in ``directory`` with ``suffix``."""
    return sorted(path for path in directory.iterdir() if path.name.endswith(suffix))


def _sdist_problems(sdist: Path, commands: tuple[str, ...]) -> list[str]:
    """Describe the manual sources one source distribution lacks or adds."""
    with tarfile.open(sdist) as archive:
        members = archive.getnames()
    required = (
        f"{_MAN_SOURCE_DIR}/docutils.conf",
        *(f"{_MAN_SOURCE_DIR}/{command}.rst" for command in commands),
        "pyproject.toml",
    )
    problems = [
        f"{sdist.name} is missing {name}"
        for name in required
        if not any(member.endswith(f"/{name}") for member in members)
    ]
    generated = sorted(
        member
        for member in members
        if f"/{_MAN_SOURCE_DIR}/" in member and member.endswith(".1")
    )
    problems.extend(
        f"{sdist.name} carries generated manual page {member}" for member in generated
    )
    return problems


def _wheel_problems(wheel: Path, commands: tuple[str, ...]) -> list[str]:
    """Describe the manual pages one wheel lacks."""
    with zipfile.ZipFile(wheel) as archive:
        members = archive.namelist()
    problems: list[str] = []
    for command in commands:
        page = f"{_MAN_DATA_SUFFIX}{command}.1"
        if not any(member.endswith(page) for member in members):
            problems.append(f"{wheel.name} is missing the {command} manual page")
    return problems


def _archive_problems(root: Path, sdists: list[Path], wheels: list[Path]) -> list[str]:
    """Collect the content problems of every archive in the build."""
    commands = console_scripts(root)
    problems: list[str] = []
    for sdist in sdists:
        try:
            problems.extend(_sdist_problems(sdist, commands))
        except (OSError, tarfile.TarError) as error:
            problems.append(f"{sdist.name} could not be read: {error}")
    for wheel in wheels:
        try:
            problems.extend(_wheel_problems(wheel, commands))
        except (OSError, zipfile.BadZipFile) as error:
            problems.append(f"{wheel.name} could not be read: {error}")
    return problems


def check_dist(dist: Path, root: Path) -> list[str]:
    """Return every problem with the distributions in ``dist``.

    Parameters
    ----------
    dist : pathlib.Path
        Directory holding the built sdist and wheels.
    root : pathlib.Path
        Repository root, used to read the console-script inventory.

    Returns
    -------
    list[str]
        One message per missing, extra, or incomplete distribution; empty when
        the build is complete.

    Examples
    --------
    >>> check_dist(Path("missing"), Path.cwd())[0]
    'missing does not exist'
    """
    if not dist.is_dir():
        return [f"{dist} does not exist"]
    sdists = _archives(dist, _SDIST_SUFFIX)
    wheels = _archives(dist, _WHEEL_SUFFIX)
    problems: list[str] = []
    if len(sdists) != 1:
        problems.append(
            f"expected exactly one source distribution, found {len(sdists)}"
        )
    if not wheels:
        problems.append("expected at least one wheel, found none")
    if sdists:
        problems.extend(_archive_problems(root, sdists, wheels))
    return problems


def main(argv: typ.Sequence[str] | None = None) -> int:
    """Validate the distribution build; report each problem as an annotation.

    Parameters
    ----------
    argv : collections.abc.Sequence[str] | None, optional
        Command-line arguments; ``sys.argv[1:]`` when ``None``.

    Returns
    -------
    int
        ``0`` when the build is complete, ``1`` otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        problems = check_dist(args.dist, args.root)
    except (KeyError, TypeError, tomllib.TOMLDecodeError) as error:
        print(f"{_ERROR}pyproject.toml could not be read: {error}", file=sys.stderr)
        return 1
    for problem in problems:
        print(f"{_ERROR}{problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
