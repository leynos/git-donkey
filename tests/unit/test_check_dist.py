"""Tests for the distribution completeness check that every build runs.

The check stands between a build and its upload, so these tests build
synthetic archives in a scratch directory and prove that a missing sdist, a
second sdist, a missing wheel, or an incomplete manual-page inventory fails
with an actionable annotation.
"""

from __future__ import annotations

import dataclasses
import io
import tarfile
import typing as typ
import zipfile

import pytest

from scripts.check_dist import check_dist, main

if typ.TYPE_CHECKING:
    from pathlib import Path

_COMMANDS = ("git-donkey", "git-track")
_REPOSITORY = "git_donkey-0.1.0"
_SDIST = f"{_REPOSITORY}.tar.gz"
_WHEEL = f"{_REPOSITORY}-py3-none-any.whl"


def _scripts(commands: tuple[str, ...]) -> str:
    """Render a ``[project.scripts]`` table for ``commands``."""
    return "".join(
        f'{command} = "git_donkey.cli:{command.replace("-", "_")}"\n'
        for command in commands
    )


def _write_pyproject(root: Path, commands: tuple[str, ...] = _COMMANDS) -> None:
    """Write the project manifest whose script inventory the check reads."""
    (root / "pyproject.toml").write_text(
        '[project]\nname = "git-donkey"\nversion = "0.1.0"\n'
        f"[project.scripts]\n{_scripts(commands)}",
        encoding="utf-8",
    )


def _sdist_members(commands: tuple[str, ...] = _COMMANDS) -> dict[str, bytes]:
    """Return a complete sdist member map for ``commands``."""
    return {
        f"{_REPOSITORY}/pyproject.toml": b"",
        f"{_REPOSITORY}/docs/man/docutils.conf": b"",
        **{f"{_REPOSITORY}/docs/man/{command}.rst": b"" for command in commands},
    }


def _wheel_members(commands: tuple[str, ...] = _COMMANDS) -> dict[str, bytes]:
    """Return a wheel member map carrying one generated page per command."""
    return {
        f"{_REPOSITORY}.data/data/share/man/man1/{command}.1": b""
        for command in commands
    }


def _write_sdist(path: Path, members: dict[str, bytes]) -> None:
    """Write a gzipped tar archive with ``members``."""
    with tarfile.open(path, "w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


def _write_wheel(path: Path, members: dict[str, bytes]) -> None:
    """Write a wheel-shaped zip archive with ``members``."""
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)


@dataclasses.dataclass(frozen=True, slots=True)
class _Build:
    """Overrides that make one synthetic build incomplete in a known way."""

    sdists: int = 1
    wheels: int = 1
    sdist_members: dict[str, bytes] | None = None
    wheel_members: dict[str, bytes] | None = None


def _sdist_names(count: int) -> list[str]:
    """Return ``count`` sdist names, the first with the canonical name."""
    return [
        _SDIST if index == 0 else f"{_REPOSITORY}-{index}.tar.gz"
        for index in range(count)
    ]


def _wheel_names(count: int) -> list[str]:
    """Return ``count`` wheel names, the first with the canonical name."""
    return [
        _WHEEL if index == 0 else f"{_REPOSITORY}-{index}-py3-none-any.whl"
        for index in range(count)
    ]


def _build(tmp_path: Path, build: _Build | None = None) -> tuple[Path, Path]:
    """Create a repository root and a dist directory holding the build."""
    build = build or _Build()
    root = tmp_path / "repo"
    root.mkdir()
    _write_pyproject(root)
    dist = root / "dist"
    dist.mkdir()
    sdist_members = (
        _sdist_members() if build.sdist_members is None else build.sdist_members
    )
    wheel_members = (
        _wheel_members() if build.wheel_members is None else build.wheel_members
    )
    for name in _sdist_names(build.sdists):
        _write_sdist(dist / name, sdist_members)
    for name in _wheel_names(build.wheels):
        _write_wheel(dist / name, wheel_members)
    return root, dist


def test_a_complete_build_passes(tmp_path: Path) -> None:
    """One sdist and one wheel with every manual page has no problem."""
    root, dist = _build(tmp_path)

    assert not check_dist(dist, root), "a complete build must have no problems"


def test_a_build_without_a_source_distribution_fails(tmp_path: Path) -> None:
    """A release cannot publish a source distribution the build never made."""
    root, dist = _build(tmp_path, _Build(sdists=0))

    assert check_dist(dist, root) == [
        "expected exactly one source distribution, found 0"
    ], "a build without an sdist must report its absence"


def test_a_build_with_two_source_distributions_fails(tmp_path: Path) -> None:
    """A second sdist makes the release's canonical bytes ambiguous."""
    root, dist = _build(tmp_path, _Build(sdists=2))

    assert check_dist(dist, root) == [
        "expected exactly one source distribution, found 2"
    ], "a build with two sdists must report the ambiguity"


def test_a_build_without_a_wheel_fails(tmp_path: Path) -> None:
    """An sdist alone is not the artefact users install."""
    root, dist = _build(tmp_path, _Build(wheels=0))

    assert check_dist(dist, root) == ["expected at least one wheel, found none"], (
        "a build without a wheel must report its absence"
    )


@pytest.mark.parametrize(
    "member",
    [
        f"{_REPOSITORY}/docs/man/git-track.rst",
        f"{_REPOSITORY}/docs/man/docutils.conf",
        f"{_REPOSITORY}/pyproject.toml",
    ],
    ids=["manual-source", "docutils-config", "project-manifest"],
)
def test_a_sdist_without_a_required_member_fails(tmp_path: Path, member: str) -> None:
    """A wheel rebuilt from the sdist would lack a required build input."""
    members = _sdist_members()
    del members[member]
    root, dist = _build(tmp_path, _Build(sdist_members=members))

    assert check_dist(dist, root) == [
        f"{_SDIST} is missing {member.split('/', 1)[1]}"
    ], "the missing manual build input must be named"


@pytest.mark.parametrize(
    "override",
    [_Build(wheel_members={}), _Build(sdist_members={})],
    ids=["empty-wheel", "empty-sdist"],
)
def test_an_empty_member_map_is_not_replaced_by_the_default(
    tmp_path: Path, override: _Build
) -> None:
    """An explicitly empty archive stays empty and is rejected."""
    root, dist = _build(tmp_path, override)

    assert check_dist(dist, root), "an empty archive must not gain the defaults"


def test_a_required_file_outside_the_sdist_root_fails(tmp_path: Path) -> None:
    """A nested copy cannot stand in for the build-root file."""
    members = _sdist_members()
    members[f"{_REPOSITORY}/examples/pyproject.toml"] = members.pop(
        f"{_REPOSITORY}/pyproject.toml"
    )
    root, dist = _build(tmp_path, _Build(sdist_members=members))

    assert check_dist(dist, root) == [f"{_SDIST} is missing pyproject.toml"], (
        "a misplaced required file must still count as missing"
    )


def test_an_sdist_with_a_top_level_file_fails(tmp_path: Path) -> None:
    """An sdist must have one top-level directory and no loose root files."""
    members = _sdist_members()
    members["pyproject.toml"] = b""
    root, dist = _build(tmp_path, _Build(sdist_members=members))

    assert check_dist(dist, root) == [
        f"{_SDIST} does not have a single top-level directory"
    ], "a loose root file must be reported"


def test_a_sdist_carrying_generated_pages_fails(tmp_path: Path) -> None:
    """Generated roff pages must never be reused as build inputs."""
    members = _sdist_members()
    members[f"{_REPOSITORY}/docs/man/git-donkey.1"] = b""
    root, dist = _build(tmp_path, _Build(sdist_members=members))

    assert check_dist(dist, root) == [
        f"{_SDIST} carries generated manual page {_REPOSITORY}/docs/man/git-donkey.1"
    ], "the stale generated page must be named"


def test_a_wheel_without_a_manual_page_fails(tmp_path: Path) -> None:
    """Every console script ships its section-one manual."""
    members = _wheel_members()
    del members[f"{_REPOSITORY}.data/data/share/man/man1/git-track.1"]
    root, dist = _build(tmp_path, _Build(wheel_members=members))

    assert check_dist(dist, root) == [
        f"{_WHEEL} is missing the git-track manual page"
    ], "the missing manual page must be named"


def test_a_wheel_page_outside_the_data_directory_fails(tmp_path: Path) -> None:
    """A page outside the wheel's data root would not install as documented."""
    members = _wheel_members()
    page = members.pop(f"{_REPOSITORY}.data/data/share/man/man1/git-track.1")
    members[f"backup/{_REPOSITORY}.data/data/share/man/man1/git-track.1"] = page
    root, dist = _build(tmp_path, _Build(wheel_members=members))

    assert check_dist(dist, root) == [
        f"{_WHEEL} is missing the git-track manual page"
    ], "a page outside the data directory must not count as installed"


def test_a_wheel_with_an_extra_manual_page_fails(tmp_path: Path) -> None:
    """An obsolete page must not reach the installed manual directory."""
    members = _wheel_members()
    members[f"{_REPOSITORY}.data/data/share/man/man1/obsolete.1"] = b""
    root, dist = _build(tmp_path, _Build(wheel_members=members))

    assert check_dist(dist, root) == [
        f"{_WHEEL} carries unexpected manual page obsolete.1"
    ], "an extra manual page must be named"


def test_the_console_script_inventory_comes_from_pyproject(
    tmp_path: Path,
) -> None:
    """A newly declared console script needs a manual page before release."""
    root, dist = _build(tmp_path)
    _write_pyproject(root, (*_COMMANDS, "git-new"))

    assert check_dist(dist, root) == [
        f"{_SDIST} is missing docs/man/git-new.rst",
        f"{_WHEEL} is missing the git-new manual page",
    ], "a newly declared script must be named on both sides"


def test_a_missing_distribution_directory_is_reported(tmp_path: Path) -> None:
    """A build that wrote nothing is a failure, not an empty success."""
    root = tmp_path / "repo"
    root.mkdir()
    _write_pyproject(root)

    assert check_dist(root / "dist", root) == [f"{root / 'dist'} does not exist"], (
        "a missing dist directory must be reported"
    )


def test_an_unreadable_archive_is_reported(tmp_path: Path) -> None:
    """A corrupt archive fails the check instead of raising through it."""
    root, dist = _build(tmp_path)
    (dist / f"{_REPOSITORY}-broken.tar.gz").write_bytes(b"not a tar archive")

    problems = check_dist(dist, root)

    assert any("could not be read" in problem for problem in problems), (
        "a corrupt archive must be reported as unreadable"
    )


def test_main_reports_every_problem_as_an_annotation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failing build prints an error annotation and returns non-zero."""
    root, dist = _build(tmp_path, _Build(sdists=0, wheels=0))

    status = main(["--dist", str(dist), "--root", str(root)])

    captured = capsys.readouterr()
    assert status == 1, "a failing check must return non-zero"
    assert not captured.out, "a failing check must not write to stdout"
    assert captured.err == (
        "::error title=check-dist::expected exactly one source distribution,"
        " found 0\n"
        "::error title=check-dist::expected at least one wheel, found none\n"
    ), "both failures must be annotated in order"


def test_main_reports_a_manifest_it_cannot_use(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unusable manifest fails with an annotation, not a traceback."""
    root, dist = _build(tmp_path)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "git-donkey"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )

    status = main(["--dist", str(dist), "--root", str(root)])

    captured = capsys.readouterr()
    assert status == 1, "an unusable manifest must fail the check"
    assert not captured.out, "an unusable manifest must not write to stdout"
    assert captured.err == (
        "::error title=check-dist::pyproject.toml could not be read: 'scripts'\n"
    ), "the failure must identify the manifest"


def test_main_accepts_a_complete_build(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A complete build prints nothing and returns zero."""
    root, dist = _build(tmp_path)

    status = main(["--dist", str(dist), "--root", str(root)])

    captured = capsys.readouterr()
    assert status == 0, "a complete build must return zero"
    assert not captured.out, "a complete build must not write to stdout"
    assert not captured.err, "a complete build must not write to stderr"
