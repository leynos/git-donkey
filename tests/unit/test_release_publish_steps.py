"""Behaviour of the release workflow's version check, collection, and PyPI steps.

The steps run their checked-in scripts in a scratch directory, with ``curl``
replaced by a stand-in serving scripted index responses, so the contract is
what the release actually executes: the tag must equal the declared version,
exactly one sdist and at least one wheel must travel together, the index
lookup retries transient failures and counts them, and files already on PyPI
are skipped by name, with nothing left meaning no upload at all.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] - fixed git commands exercise the tag check.
import sys
import typing as typ

import pytest

from tests.release_workflow import (
    FakeRelease,
    fake_release,
    link_scripts,
    outputs,
    run_bash,
    run_version_check,
    step_script,
)

if typ.TYPE_CHECKING:
    import pathlib as pth

_COLLECT_STEP = ("attest", "Collect release artefacts")
_INDEX_STEP = ("publish-pypi", "Fetch the PyPI index")
_SKIP_STEP = ("publish-pypi", "Skip artefacts already on PyPI")
_ARTEFACTS = ("git_donkey-1-cp313-linux.whl", "git_donkey-1-py3-none-any.whl")
_SDIST = "git_donkey-1.tar.gz"
_ALL = (*_ARTEFACTS, _SDIST)
_RETRY_ATTEMPTS = 3

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="The release workflow's steps run under Bash on Linux.",
)


def _cli(*arguments: str) -> str:
    """Build a direct CLI invocation using the pytest interpreter."""
    return " ".join((
        shlex.quote(sys.executable),
        "-m",
        "scripts.release_assets_cli",
        *arguments,
    ))


def _touch(directory: pth.Path, *names: str) -> None:
    """Create artefact files named ``names`` under ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_bytes(name.encode())


@pytest.mark.parametrize(
    ("version", "expected_prerelease"),
    [
        ("0.2.0-beta1", "true"),
        ("0.2.0b1", "true"),
        ("1.0.0-rc.2", "true"),
        ("1.0.0a3", "true"),
        ("1.0.0.post1.dev2", "true"),
        ("1.2.3", "false"),
        ("1.2.3.post1", "false"),
        ("1.2.3+rc1", "false"),
        ("10.20.30", "false"),
    ],
)
def test_a_matching_tag_passes_and_reports_its_prerelease_status(
    tmp_path: pth.Path, version: str, expected_prerelease: str
) -> None:
    """The tag-derived version must equal the declared one, in either spelling."""
    completed, written = run_version_check(tmp_path, version, version)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert written == {"prerelease": expected_prerelease}, (
        f"{version} must be reported as prerelease={expected_prerelease}"
    )


@pytest.mark.parametrize(
    ("declared", "tag_version"),
    [("1.2.2", "1.2.3"), ("0.2.0-beta1", "0.2.0b1"), ("0.2.0-beta1", "0.2.0")],
    ids=["older-manifest", "respelled-prerelease", "dropped-prerelease"],
)
def test_a_mismatched_tag_fails_before_anything_is_published(
    tmp_path: pth.Path, declared: str, tag_version: str
) -> None:
    """A tag naming another version stops the release with an annotation."""
    completed, written = run_version_check(tmp_path, declared, tag_version)

    assert completed.returncode != 0, "a mismatched tag must fail the release"
    assert completed.stdout == (
        "::error file=pyproject.toml,title=Tag/pyproject.toml mismatch::"
        f"Tag version {tag_version} does not match pyproject.toml version "
        f"{declared}\n"
    ), "the failure must identify the manifest and both versions"
    assert not written, "a failed check must not report a prerelease status"


def _git(cwd: pth.Path, *arguments: str) -> str:
    """Run one fixed git command in ``cwd`` and return its stripped output."""
    git = shutil.which("git")
    assert git is not None, "the tag check test requires git"
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv
        [git, *arguments], cwd=cwd, check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _create_tag(cwd: pth.Path, *, annotated: bool, force: bool = False) -> None:
    """Create or move ``v1.0.0`` as a lightweight or annotated tag."""
    arguments = ("-f",) if force else ()
    if annotated:
        _git(cwd, "tag", *arguments, "-a", "v1.0.0", "-m", "release")
    else:
        _git(cwd, "tag", *arguments, "v1.0.0")


@pytest.mark.parametrize("annotated", [False, True], ids=["lightweight", "annotated"])
def test_the_tag_check_accepts_the_live_commit_and_rejects_a_moved_tag(
    tmp_path: pth.Path, *, annotated: bool
) -> None:
    """A run is bound to the commit the tag currently names."""
    remote = tmp_path / "remote.git"
    clone = tmp_path / "clone"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(tmp_path, "init", str(clone))
    _git(clone, "config", "user.name", "Test")
    _git(clone, "config", "user.email", "test@example.test")
    _git(clone, "config", "commit.gpgsign", "false")
    _git(clone, "config", "tag.gpgSign", "false")
    (clone / "file.txt").write_text("one", encoding="utf-8")
    _git(clone, "add", "file.txt")
    _git(clone, "commit", "-m", "first")
    first = _git(clone, "rev-parse", "HEAD")
    _create_tag(clone, annotated=annotated)
    _git(clone, "remote", "add", "origin", str(remote))
    _git(clone, "push", "origin", "HEAD:main", "--tags")

    (clone / "file.txt").write_text("two", encoding="utf-8")
    _git(clone, "commit", "-am", "second")
    second = _git(clone, "rev-parse", "HEAD")
    _create_tag(clone, annotated=annotated, force=True)
    _git(clone, "push", "--force", "origin", "HEAD:main", "v1.0.0")

    script = step_script(
        "publish-pypi", "Check the tag still points to this run's commit"
    )
    moved = run_bash(script, clone, {"GITHUB_REF_NAME": "v1.0.0", "GITHUB_SHA": first})
    current = run_bash(
        script, clone, {"GITHUB_REF_NAME": "v1.0.0", "GITHUB_SHA": second}
    )

    assert moved.returncode != 0, "a moved tag must stop the run"
    assert moved.stderr == (
        f"::error title=tag-moved::Tag v1.0.0 points to {second}, "
        f"not this run's {first}.\n"
    ), "the failure must name both the tag's commit and the run's commit"
    assert current.returncode == 0, current.stderr


def test_collection_gathers_the_sdist_with_every_wheel(tmp_path: pth.Path) -> None:
    """Wheels from every artefact directory and the one sdist are collected."""
    _touch(tmp_path / "dist" / "wheels-pure", _ARTEFACTS[1], _SDIST)
    _touch(tmp_path / "dist" / "wheels-native-linux", _ARTEFACTS[0])

    completed = run_bash(step_script(*_COLLECT_STEP), tmp_path)

    assert completed.returncode == 0, completed.stderr
    collected = sorted(path.name for path in (tmp_path / "dist" / "publish").iterdir())
    assert collected == sorted((*_ARTEFACTS, _SDIST)), (
        "every wheel and the sdist must be staged"
    )


@pytest.mark.parametrize(
    "sdists",
    [(), (_SDIST, "git_donkey-2.tar.gz")],
    ids=["no-sdist", "two-sdists"],
)
def test_collection_fails_without_exactly_one_sdist(
    tmp_path: pth.Path, sdists: tuple[str, ...]
) -> None:
    """A release that lost its sdist, or gained a second, fails before upload."""
    _touch(tmp_path / "dist" / "wheels-pure", _ARTEFACTS[1], *sdists)

    completed = run_bash(step_script(*_COLLECT_STEP), tmp_path)

    assert completed.returncode != 0, "the release needs exactly one sdist"
    assert completed.stderr.splitlines()[0] == (
        "Expected exactly one source distribution."
    ), "the failure must explain the missing or duplicated sdist"


def test_collection_fails_when_no_wheel_was_built(tmp_path: pth.Path) -> None:
    """A build with only an sdist cannot produce an installable release."""
    _touch(tmp_path / "dist" / "wheels-pure", _SDIST)

    completed = run_bash(step_script(*_COLLECT_STEP), tmp_path)

    assert completed.returncode != 0, "the release needs at least one wheel"
    assert completed.stderr.splitlines()[0] == "Expected at least one wheel.", (
        "the failure must explain the missing wheel"
    )


def _fetch_and_skip(
    tmp_path: pth.Path, statuses: str, published: tuple[str, ...] = ()
) -> tuple[FakeRelease, subprocess.CompletedProcess[str], dict[str, str]]:
    """Run the index fetch, then the skip step if the fetch succeeded."""
    fake = fake_release(tmp_path)
    link_scripts(tmp_path)
    _touch(tmp_path / "dist" / "publish", *_ARTEFACTS, _SDIST)
    _touch(fake.pypi, *published)
    (tmp_path / "github-state" / "files").mkdir(parents=True)
    (tmp_path / "github-state" / "assets.json").write_text('{"assets": []}', "utf-8")
    env = fake.env(FAKE_CURL_STATUSES=statuses)
    completed = run_bash(step_script(*_INDEX_STEP), tmp_path, env)
    if completed.returncode == 0:
        completed = run_bash(step_script(*_SKIP_STEP), tmp_path, env)
    return fake, completed, outputs(tmp_path / "github-output")


def _remaining(tmp_path: pth.Path) -> list[str]:
    """Return the artefact names left for the PyPI upload."""
    return sorted(path.name for path in (tmp_path / "dist" / "publish").iterdir())


@pytest.mark.parametrize(
    ("published", "remaining", "has_remaining"),
    [
        (_ALL[1:], [_ALL[0]], "true"),
        (_ALL, [], "false"),
    ],
    ids=["uploads-remaining", "nothing-left"],
)
def test_artefacts_already_on_pypi_are_skipped_by_name(
    tmp_path: pth.Path,
    published: tuple[str, ...],
    remaining: list[str],
    has_remaining: str,
) -> None:
    """Only unpublished names remain, and the step says whether any do."""
    _, completed, written = _fetch_and_skip(tmp_path, "200", published)

    assert completed.returncode == 0, completed.stderr
    assert _remaining(tmp_path) == remaining, "published names must be dropped"
    assert (
        "::notice::Skipping git_donkey-1.tar.gz: already on PyPI\n" in completed.stdout
    ), "a published name must be reported as a complete skipped line"
    assert written == {"attempts": "1", "status": "200", "remaining": has_remaining}, (
        "the step must report the index result and whether anything remains"
    )


def test_a_project_absent_from_pypi_reads_as_an_empty_index(
    tmp_path: pth.Path,
) -> None:
    """A 404 is a first release: nothing is skipped and every file uploads."""
    _, completed, written = _fetch_and_skip(tmp_path, "404")

    assert completed.returncode == 0, completed.stderr
    index = json.loads((tmp_path / "pypi-index.json").read_text(encoding="utf-8"))
    assert index == {"files": []}, "a missing project must read as an empty index"
    assert _remaining(tmp_path) == sorted((*_ARTEFACTS, _SDIST)), (
        "every artefact must remain for the first upload"
    )
    assert written == {"attempts": "1", "status": "404", "remaining": "true"}, (
        "the first upload must report what remains"
    )


def test_stage_pypi_requires_the_provenance_directory(tmp_path: pth.Path) -> None:
    """The CLI cannot stage carried assets without verifier output input."""
    link_scripts(tmp_path)
    _touch(tmp_path / "dist" / "publish", *_ARTEFACTS, _SDIST)
    (tmp_path / "assets.json").write_text('{"assets": []}', encoding="utf-8")
    (tmp_path / "pypi-index.json").write_text('{"files": []}', encoding="utf-8")
    completed = run_bash(
        _cli(
            "stage-pypi",
            "--publish",
            "dist/publish",
            "--assets",
            "assets.json",
            "--github-files",
            "github-files",
            "--index",
            "pypi-index.json",
        ),
        tmp_path,
    )

    assert completed.returncode == 1, "stage-pypi must reject a missing provenance path"
    assert completed.stderr == (
        "::error title=release-assets::--provenance is required for stage-pypi\n"
    ), "the CLI must report exactly that provenance records are required"


@pytest.mark.parametrize(
    "command",
    [
        "stage-pypi",
        "stage-github",
        "check-fetched",
        "check-provenance",
        "destinations-complete",
        "release-complete",
    ],
)
def test_the_index_dependent_commands_require_the_index(
    tmp_path: pth.Path, command: str
) -> None:
    """Without the index each command would consider no PyPI file at all."""
    link_scripts(tmp_path)
    _touch(tmp_path / "dist" / "publish", *_ARTEFACTS, _SDIST)
    (tmp_path / "assets.json").write_text('{"assets": []}', encoding="utf-8")
    completed = run_bash(
        _cli(
            command,
            "--publish",
            "dist/publish",
            "--assets",
            "assets.json",
            "--output",
            "github-upload",
            "--github-files",
            "github-files",
            "--provenance",
            "pypi-provenance",
            "--bundles",
            "dist/attestations",
        ),
        tmp_path,
    )

    assert completed.returncode == 1, f"{command} must reject a missing index"
    assert completed.stderr == (
        f"::error title=release-assets::--index is required for {command}\n"
    ), "the CLI must report exactly that the command needs the PyPI index"


def test_bundle_coverage_requires_the_bundle_directory(tmp_path: pth.Path) -> None:
    """The coverage check cannot silently treat a missing directory as clear."""
    link_scripts(tmp_path)
    _touch(tmp_path / "dist" / "publish", *_ARTEFACTS, _SDIST)
    (tmp_path / "assets.json").write_text('{"assets": []}', encoding="utf-8")
    completed = run_bash(
        _cli(
            "bundle-coverage",
            "--publish",
            "dist/publish",
            "--assets",
            "assets.json",
        ),
        tmp_path,
    )

    assert completed.returncode == 1, "bundle-coverage must reject a missing path"
    assert completed.stderr == (
        "::error title=release-assets::--bundles is required for bundle-coverage\n"
    ), "the CLI must report exactly that the bundle directory is required"


def test_a_transient_failure_is_retried_and_counted(tmp_path: pth.Path) -> None:
    """A 503 then a 429 then a 200 succeeds on the third attempt."""
    fake, completed, written = _fetch_and_skip(tmp_path, "503,429,200")

    assert completed.returncode == 0, completed.stderr
    assert written["attempts"] == str(_RETRY_ATTEMPTS), (
        "the step must report every attempt"
    )
    assert written["status"] == "200", "the successful status must be reported"
    assert len(fake.calls("curl")) == _RETRY_ATTEMPTS, "each retry must reach the index"


@pytest.mark.parametrize(
    ("statuses", "attempts"),
    [("500", "6"), ("000", "6"), ("403", "1")],
    ids=["server-error", "network-error", "client-error"],
)
def test_a_failed_index_lookup_stops_the_upload(
    tmp_path: pth.Path, statuses: str, attempts: str
) -> None:
    """A persistent 5xx or network error, or any other 4xx, fails the job."""
    fake, completed, written = _fetch_and_skip(tmp_path, statuses)

    assert completed.returncode != 0, f"HTTP {statuses} must fail the lookup"
    assert completed.stderr == (f"PyPI simple index returned HTTP {statuses}.\n"), (
        "the failure must name the index response exactly"
    )
    assert written == {"attempts": attempts, "status": statuses}, (
        "the step must report the bounded attempt count and final status"
    )
    assert len(fake.calls("curl")) == int(attempts), "the loop must stay bounded"
    assert _remaining(tmp_path) == sorted((*_ARTEFACTS, _SDIST)), (
        "nothing may be skipped"
    )
