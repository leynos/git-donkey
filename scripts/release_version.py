#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Check ``pyproject.toml``'s version against the release tag.

``release.yml`` runs this in its ``check-version`` job: the tag's version,
with one leading ``v`` removed, must equal the declared string exactly, and the
step reports whether the version is a pre-release. Packaging requires the
declared version to be a PEP 440 version, so the pre-release classification
follows PEP 440 labels; another spelling cannot match the manifest. It uses
the runner's preinstalled ``python3`` and the standard library only, as the
other release helpers do and for the same reason: no third-party code is
resolved in the release path.

Examples
--------
>>> is_prerelease("0.2.0-beta1"), is_prerelease("1.2.3+rc1")
(True, False)
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: Every PEP 440 label, longest spelling first, so a left-to-right scan splits
#: a run of letters as PEP 440 does: ``1adev3`` reads as ``a`` then ``dev``,
#: and ``preview`` never as ``pre``. A hyphenated spelling such as
#: ``0.2.0-beta1`` uses the same labels.
_LABEL = re.compile(r"preview|alpha|beta|post|pre|rev|dev|rc|a|b|c|r")
#: The labels that make a pre-release or development release; the others
#: (``post``, ``rev``, ``r``) spell a post-release.
_PRERELEASE_LABELS = frozenset({
    "preview",
    "alpha",
    "beta",
    "pre",
    "dev",
    "rc",
    "a",
    "b",
    "c",
})
_REQUIRED = ("TAG_VERSION", "GITHUB_OUTPUT")


def is_prerelease(version: str) -> bool:
    """Return whether ``version`` names a pre-release or development release.

    Parameters
    ----------
    version : str
        A PEP 440 release version, as ``pyproject.toml`` declares.

    Returns
    -------
    bool
        ``True`` for a marker in the public version; a ``+local`` label never
        makes a pre-release.

    Examples
    --------
    >>> is_prerelease("1.2.3"), is_prerelease("1.2.3rc1")
    (False, True)
    """
    public = version.lower().partition("+")[0]
    return any(label in _PRERELEASE_LABELS for label in _LABEL.findall(public))


def _missing_environment(environ: cabc.Mapping[str, str]) -> list[str]:
    """Return the required environment variable names that are absent."""
    return [name for name in _REQUIRED if name not in environ]


def main(environ: cabc.Mapping[str, str], root: Path) -> int:
    """Compare the declared version with the tag's; report pre-release status.

    Parameters
    ----------
    environ : collections.abc.Mapping[str, str]
        Environment holding ``TAG_VERSION`` and ``GITHUB_OUTPUT``.
    root : pathlib.Path
        Repository root holding ``pyproject.toml``.

    Returns
    -------
    int
        ``0`` when the tag matches, ``1`` otherwise.
    """
    missing = _missing_environment(environ)
    if missing:
        print(
            "::error title=release-version::missing environment variable(s): "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return 1
    expected = environ["TAG_VERSION"].removeprefix("v")
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    declared = pyproject["project"]["version"]
    if declared != expected:
        print(
            "::error file=pyproject.toml,title=Tag/pyproject.toml mismatch::"
            f"Tag version {expected} does not match pyproject.toml version {declared}"
        )
        return 1
    prerelease = str(is_prerelease(declared)).lower()
    with Path(environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"prerelease={prerelease}\n")
    print(f"Release tag {expected} matches pyproject.toml.")
    return 0


if __name__ == "__main__":
    sys.exit(main(os.environ, Path.cwd()))
