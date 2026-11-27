"""Tests for the tag/version and GitHub CLI version release gates.

The release compares the pushed tag with ``pyproject.toml`` as an exact string
and refuses to verify provenance with a GitHub CLI older than 2.102.0. Both
checks are small standard-library programs so they can run before any
third-party code is resolved, and these tests pin their failure messages.
"""

from __future__ import annotations

import typing as typ

import pytest

from scripts import release_gh_version, release_version

if typ.TYPE_CHECKING:
    from pathlib import Path


def _write_pyproject(root: Path, version: str) -> None:
    """Write a minimal project manifest declaring ``version``."""
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "git-donkey"\nversion = "{version}"\n',
        encoding="utf-8",
    )


def _outputs(path: Path) -> dict[str, str]:
    """Parse the ``name=value`` lines a step appended to its output file."""
    if not path.exists():
        return {}
    lines = path.read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line)


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("0.2.0-beta1", True),
        ("0.2.0b1", True),
        ("1.0.0-rc.2", True),
        ("1.0.0a3", True),
        ("1.0.0.post1.dev2", True),
        ("1.2.3", False),
        ("1.2.3.post1", False),
        ("1.2.3+rc1", False),
        ("10.20.30", False),
    ],
)
def test_prerelease_labels_follow_pep_440(version: str, *, expected: bool) -> None:
    """Every PEP 440 marker is recognised, and a local label is not one."""
    assert release_version.is_prerelease(version) is expected, (
        f"{version} must be reported as a pre-release={expected}"
    )


@pytest.mark.parametrize(
    ("tag", "declared"),
    [
        ("v1.2.3", "1.2.3"),
        ("1.2.3", "1.2.3"),
        ("v0.2.0-beta1", "0.2.0-beta1"),
    ],
)
def test_a_matching_tag_passes_and_reports_prerelease(
    tmp_path: Path, tag: str, declared: str
) -> None:
    """The tag's version is compared with the manifest minus one leading v."""
    _write_pyproject(tmp_path, declared)
    output = tmp_path / "output"

    status = release_version.main(
        {"TAG_VERSION": tag, "GITHUB_OUTPUT": str(output)}, tmp_path
    )

    assert status == 0, "a matching tag must pass"
    assert _outputs(output) == {
        "prerelease": str(release_version.is_prerelease(declared)).lower()
    }, "the step must report whether the release is a pre-release"


@pytest.mark.parametrize(
    ("tag", "declared"),
    [
        ("v1.2.3", "1.2.2"),
        ("v0.2.0b1", "0.2.0-beta1"),
        ("v0.2.0", "0.2.0-beta1"),
    ],
    ids=["other-version", "respelled-prerelease", "dropped-prerelease"],
)
def test_a_mismatched_tag_fails_before_anything_is_published(
    tmp_path: Path, tag: str, declared: str
) -> None:
    """An exact string comparison rejects another spelling of the version."""
    _write_pyproject(tmp_path, declared)
    output = tmp_path / "output"

    status = release_version.main(
        {"TAG_VERSION": tag, "GITHUB_OUTPUT": str(output)}, tmp_path
    )

    assert status == 1, "a mismatched tag must fail the release"
    assert not output.exists(), "a failed check must report no prerelease status"


def test_a_missing_environment_variable_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A workflow wiring mistake fails explicitly instead of with a traceback."""
    _write_pyproject(tmp_path, "1.2.3")

    status = release_version.main({"TAG_VERSION": "v1.2.3"}, tmp_path)

    assert status == 1, "a missing output path must fail the check"
    assert "::error title=release-version::missing environment variable(s)" in (
        capsys.readouterr().err
    ), "the failure must name the missing environment"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("gh version 2.102.0 (2026-01-15)", (2, 102, 0)),
        ("gh version 2.97.0 (2026-07-31)", (2, 97, 0)),
        ("gh version 3.0.0-beta1 (2027-01-01)", (3, 0, 0)),
        ("release notes unavailable", None),
    ],
)
def test_the_github_cli_version_is_parsed(
    line: str, expected: tuple[int, int, int] | None
) -> None:
    """The release number comes from the first line's version token."""
    assert release_gh_version.parse_version(line) == expected, (
        "the parsed release must match the version the CLI printed"
    )


@pytest.mark.parametrize(
    ("version", "status"),
    [("2.102.0", 0), ("2.103.1", 0), ("2.101.9", 1), ("unknown", 1)],
)
def test_the_github_cli_minimum_is_enforced(version: str, status: int) -> None:
    """The verifier requires a CLI release with the attestation fixes."""
    lines = [f"gh version {version} (fake)"] if version != "unknown" else ["unknown"]

    assert release_gh_version.main(lines) == status, (
        f"GitHub CLI {version} must produce status {status}"
    )


def test_an_old_github_cli_names_the_minimum_and_the_found_release(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The failure explains which CLI is required and which one ran."""
    status = release_gh_version.main(["gh version 2.101.9 (fake)"])

    assert status == 1, "an old CLI must fail the gate"
    assert (
        "GitHub CLI 2.102.0 or newer is required; found 2.101.9."
        in capsys.readouterr().err
    ), "the diagnostic must name both releases"
