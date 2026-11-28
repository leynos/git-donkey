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
import collections
import sys
import tarfile
import tomllib
import typing as typ
import zipfile
from pathlib import Path

if typ.TYPE_CHECKING:
    import collections.abc as cabc

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


def _sdist_files(sdist: Path) -> list[str]:
    """Return the sdist's non-directory member names.

    An explicit entry for the top-level directory reaches :mod:`tarfile`
    without its trailing slash, so directory entries are excluded rather than
    mistaken for loose top-level files.

    Returns
    -------
    list[str]
        The archive's member names that are not directory entries.
    """
    with tarfile.open(sdist) as archive:
        return [member.name for member in archive.getmembers() if not member.isdir()]


def _sdist_root(files: list[str]) -> str | None:
    """Return the sdist's single top-level directory, if it has exactly one."""
    if any("/" not in name for name in files):
        return None
    roots = {name.split("/", 1)[0] for name in files}
    return next(iter(roots)) if len(roots) == 1 else None


def _sdist_problems(sdist: Path, commands: tuple[str, ...]) -> list[str]:
    """Describe the manual sources one source distribution lacks or adds."""
    files = _sdist_files(sdist)
    root = _sdist_root(files)
    if root is None:
        return [f"{sdist.name} does not have a single top-level directory"]
    required = (
        f"{root}/{_MAN_SOURCE_DIR}/docutils.conf",
        *(f"{root}/{_MAN_SOURCE_DIR}/{command}.rst" for command in commands),
        f"{root}/pyproject.toml",
    )
    present = set(files)
    missing = [name for name in required if name not in present]
    problems = [f"{sdist.name} is missing {name[len(root) + 1 :]}" for name in missing]
    generated = sorted(
        name
        for name in files
        if name.startswith(f"{root}/{_MAN_SOURCE_DIR}/") and name.endswith(".1")
    )
    problems.extend(
        f"{sdist.name} carries generated manual page {name}" for name in generated
    )
    return problems


def _wheel_data_prefix(wheel: Path) -> str:
    """Return the wheel's root data-directory prefix for manual pages."""
    stem = wheel.name.removesuffix(_WHEEL_SUFFIX)
    distribution, version = stem.split("-", 2)[:2]
    return f"{distribution}-{version}{_MAN_DATA_SUFFIX}"


def _wheel_problems(wheel: Path, commands: tuple[str, ...]) -> list[str]:
    """Describe the manual pages one wheel lacks or installs unexpectedly."""
    with zipfile.ZipFile(wheel) as archive:
        members = archive.namelist()
    prefix = _wheel_data_prefix(wheel)
    pages = [member for member in members if member.endswith(".1")]
    installed = {
        member.removeprefix(prefix).removesuffix(".1")
        for member in pages
        if member.startswith(prefix)
    }
    expected = set(commands)
    problems = [
        f"{wheel.name} is missing the {command} manual page"
        for command in commands
        if command not in installed
    ]
    problems.extend(
        f"{wheel.name} carries unexpected manual page {name}.1"
        for name in sorted(installed - expected)
    )
    problems.extend(
        f"{wheel.name} carries manual page outside {prefix}: {page}"
        for page in sorted(page for page in pages if not page.startswith(prefix))
    )
    problems.extend(
        f"{wheel.name} carries duplicate manual page {page}"
        for page, count in sorted(collections.Counter(pages).items())
        if count > 1
    )
    return problems


def _read_archive(
    archive: Path,
    checker: cabc.Callable[[Path, tuple[str, ...]], list[str]],
    commands: tuple[str, ...],
) -> list[str]:
    """Return one archive's problems, or its read failure as a problem."""
    try:
        return checker(archive, commands)
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as error:
        return [f"{archive.name} could not be read: {error}"]


def _archive_problems(root: Path, sdists: list[Path], wheels: list[Path]) -> list[str]:
    """Collect the content problems of every archive in the build."""
    commands = console_scripts(root)
    checkers = [
        *((sdist, _sdist_problems) for sdist in sdists),
        *((wheel, _wheel_problems) for wheel in wheels),
    ]
    return [
        problem
        for archive, checker in checkers
        for problem in _read_archive(archive, checker, commands)
    ]


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
