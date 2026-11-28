#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Command-line entry point for the release-asset reconciliation commands.

``release.yml`` runs this module from the workspace root as
``python3 -m scripts.release_assets_cli``, with the runner's preinstalled
``python3`` and the standard library alone: it executes inside the job that
holds the PyPI publishing credential, so it resolves no third-party code at
release time, and every network call stays in the workflow's ``curl`` and
``gh`` steps. That is the documented exception to the scripting standards'
Cyclopts and Python 3.13 defaults. Each subcommand is one step of
``release.yml``: ``carryover`` and ``release-complete`` (``draft-release``),
``stage-pypi`` (``publish-pypi``), then ``stage-github``, ``check-fetched``,
``check-provenance``, ``bundle-coverage``, and ``verify``
(``publish-release``). The reconciliation logic itself lives in
``scripts/release_assets.py``, ``scripts/release_bundle.py``, and
``scripts/release_github_upload.py``; this module only wires it to ``argparse``
and the workflow's ``GITHUB_OUTPUT`` protocol.
"""

from __future__ import annotations

import argparse
import sys
import typing as typ
from pathlib import Path

from scripts.release_assets import (
    ReleaseAssetError,
    State,
    carryover,
    has_bundle,
    incomplete_github_assets,
    local_names,
    mismatches,
    stage_pypi,
    verify,
    write_output,
)
from scripts.release_bundle import combined_coverage, has_combined_bundle
from scripts.release_github_upload import (
    check_fetched,
    check_provenance,
    stage_github,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc


def _parser() -> argparse.ArgumentParser:
    """Build the command-line interface the release workflow calls."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=_COMMANDS)
    parser.add_argument("--publish", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--index", type=Path)
    parser.add_argument("--github-files", type=Path)
    parser.add_argument("--provenance", type=Path)
    parser.add_argument("--bundles", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def _path(args: argparse.Namespace, option: str) -> Path:
    """Return a path option the chosen command requires."""
    value = getattr(args, option)
    if value is None:
        msg = f"--{option.replace('_', '-')} is required for {args.command}"
        raise ReleaseAssetError(msg)
    return value


def _require_complete_uploads(args: argparse.Namespace) -> None:
    """Refuse reconciliation while the release holds an incomplete upload.

    The destination metadata check is the exception: it treats the incomplete
    asset as a confirmed incompleteness so a published release can be redrafted.

    Raises
    ------
    ReleaseAssetError
        If the release listing holds an incomplete upload.
    """
    if args.command == "destinations-complete":
        return
    incomplete = incomplete_github_assets(args.assets)
    if incomplete:
        msg = (
            "GitHub Release holds incomplete upload(s): "
            f"{', '.join(incomplete)}; delete the named asset(s) and rerun"
        )
        raise ReleaseAssetError(msg)


def _carryover(state: State, args: argparse.Namespace) -> None:
    """Print the names to download from the GitHub Release, one per line."""
    del args
    for name in carryover(state):
        print(name)


def _stage_pypi(state: State, args: argparse.Namespace) -> None:
    """Stage PyPI's upload set and report whether anything remains."""
    write_output(
        "remaining",
        value=stage_pypi(
            state,
            _path(args, "github_files"),
            _path(args, "provenance"),
        ),
    )


def _stage_github(state: State, args: argparse.Namespace) -> None:
    """Stage GitHub's upload set and print PyPI's files as TSV rows."""
    # Without the index PyPI looks empty, so every artefact would be staged
    # from the local build instead of fetched from PyPI's published bytes.
    _path(args, "index")
    fetch = stage_github(state, _path(args, "output"))
    for name, url in fetch:
        print(f"{name}\t{url}")
    write_output("files", value=bool(fetch))
    write_output("pending", value=bool(fetch) or not has_bundle(state.github))


def _check_fetched(state: State, args: argparse.Namespace) -> None:
    """Check every fetched PyPI file against PyPI's digest."""
    # Without the index there is no digest to compare against, so the check
    # would pass over every fetched file without examining one.
    _path(args, "index")
    check_fetched(state, _path(args, "output"))


def _check_provenance(state: State, args: argparse.Namespace) -> None:
    """Check every fetched PyPI file against approved build provenance."""
    # Without the index the plan holds no PyPI names, so there would be
    # nothing to check; refuse rather than silently verifying nothing.
    _path(args, "index")
    check_provenance(
        state,
        _path(args, "output"),
        _path(args, "provenance"),
    )


def _verify(state: State, args: argparse.Namespace) -> None:
    """Fail on any artefact or bundle the two destinations disagree on."""
    problems = verify(state, _path(args, "output"))
    if problems:
        raise ReleaseAssetError("; ".join(problems))
    print("PyPI and the GitHub Release hold the same bytes for every artefact.")


def _destinations_complete(state: State, args: argparse.Namespace) -> None:
    """Report whether PyPI and the GitHub Release hold the same digests."""
    _path(args, "index")
    pypi = {name: file.sha256 for name, file in state.pypi.items()}
    problems = mismatches(local_names(state.publish), pypi, state.github)
    for problem in problems:
        print(f"::notice::{problem}")
    write_output("complete", value=not problems)


def _release_complete(state: State, args: argparse.Namespace) -> None:
    """Report whether a published release already holds every canonical byte."""
    _path(args, "index")
    bundles = _path(args, "bundles")
    paths = sorted(bundles.iterdir()) if bundles.is_dir() else []
    problems = verify(state, _path(args, "output"))
    if not has_combined_bundle(paths):
        problems.append("the GitHub Release holds no combined attestation bundle")
    problems.extend(
        f"{name} is not covered by the attached bundle"
        for name in combined_coverage(state.github, paths)
    )
    for problem in problems:
        print(f"::notice::{problem}")
    write_output("complete", value=not problems)


def _bundle_coverage(state: State, args: argparse.Namespace) -> None:
    """Report whether the attached bundle covers every release distribution."""
    bundles = _path(args, "bundles")
    paths = sorted(bundles.iterdir()) if bundles.is_dir() else []
    combined = has_combined_bundle(paths)
    missing = combined_coverage(state.github, paths)
    if not combined:
        print("::notice::No combined attestation bundle is attached.")
    for name in missing:
        print(f"::notice::{name} is not covered by the attached bundle")
    write_output("covered", value=combined and not missing)


_COMMANDS: dict[str, cabc.Callable[[State, argparse.Namespace], None]] = {
    "carryover": _carryover,
    "stage-pypi": _stage_pypi,
    "stage-github": _stage_github,
    "check-fetched": _check_fetched,
    "check-provenance": _check_provenance,
    "verify": _verify,
    "destinations-complete": _destinations_complete,
    "release-complete": _release_complete,
    "bundle-coverage": _bundle_coverage,
}


def main(argv: cabc.Sequence[str] | None = None) -> int:
    """Run one reconciliation command; report failures as annotations."""
    args = _parser().parse_args(argv)
    try:
        state = State.load(args.publish, args.index, args.assets)
        _require_complete_uploads(args)
        _COMMANDS[args.command](state, args)
    except (ReleaseAssetError, OSError, KeyError, ValueError) as error:
        print(f"::error title=release-assets::{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
