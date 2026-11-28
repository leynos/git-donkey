"""Shared constants, fixtures, and helpers for the release reconciliation tests.

Each test runs the checked-in steps of ``draft-release``, ``publish-pypi``, and
``publish-release`` in order, against ``gh`` and ``curl`` stand-ins whose
directories hold the GitHub Release's assets and PyPI's files.
"""

from __future__ import annotations

import shutil
import typing as typ

import pytest

from scripts.release_assets import BUNDLE_SUFFIX
from tests.release_workflow import (
    FakeRelease,
    fake_release,
    link_scripts,
    outputs,
    run_bash,
    step_script,
)

if typ.TYPE_CHECKING:
    import pathlib as pth

_TAG = "v1.2.3"
_BUNDLE = f"git-donkey-v1.2.3-run7-1{BUNDLE_SUFFIX}"
_ASSEMBLED_BUNDLE = "git-donkey-v1.2.3-run7-1.attestations.jsonl"
_EXISTING_BUNDLE = "git-donkey-v1.2.3-run6-1.attestations.jsonl"
#: One name per reconciliation case: PyPI only, GitHub only, neither, both.
_PYPI_ONLY, _GITHUB_ONLY, _NEITHER, _BOTH = (
    "git_donkey-1-cp313-linux.whl",
    "git_donkey-1-cp313-macos.whl",
    "git_donkey-1-py3-none-any.whl",
    "git_donkey-1.tar.gz",
)
_ARTEFACTS = (_PYPI_ONLY, _GITHUB_ONLY, _NEITHER, _BOTH)
#: The provenance gate sits after the index-digest check and before the
#: upload, so nothing unverified can reach the release.
_PROVENANCE_STEP = "Verify provenance of PyPI bytes"
_ASSEMBLY_STEP = "Assemble the release attestations"
_UPLOAD_STEP = "Upload the assets the release lacks"
_PRE_PROVENANCE = (
    "Fetch the PyPI index",
    "List the release assets",
    "Check the attached bundle coverage",
    "Stage the assets the release lacks",
    "Check the fetched PyPI bytes",
)
_PUBLISH_STEPS = (*_PRE_PROVENANCE, _PROVENANCE_STEP)
_PREDICATE_TYPE = "https://slsa.dev/provenance/v1"


def _local(name: str) -> bytes:
    """Return the bytes this run's build produced for ``name``."""
    return f"rebuilt {name}".encode()


def _job_directory(fake: FakeRelease, job: str) -> tuple[pth.Path, dict[str, str]]:
    """Stage one job's workspace with this run's artefacts and bundle."""
    cwd = fake.root / job
    for name in _ARTEFACTS:
        (cwd / "dist" / "publish").mkdir(parents=True, exist_ok=True)
        (cwd / "dist" / "publish" / name).write_bytes(_local(name))
    (cwd / "dist" / "attestations").mkdir(parents=True)
    (cwd / "dist" / "attestations" / _BUNDLE).write_bytes(b"bundle")
    link_scripts(cwd)
    return cwd, fake.env(GITHUB_OUTPUT=str(cwd / "output"))


def _step(job: str, name: str, cwd: pth.Path, env: dict[str, str]) -> None:
    """Run one step and require it to succeed."""
    completed = run_bash(step_script(job, name), cwd, env)
    if completed.returncode != 0:
        pytest.fail(f"{job}/{name}: {completed.stderr}")


def _draft_and_publish_pypi(fake: FakeRelease) -> None:
    """Run the snapshot, the PyPI staging, and the modelled PyPI upload."""
    draft, env = _job_directory(fake, "draft-release")
    _step("draft-release", "Snapshot the release assets", draft, env)
    _step("draft-release", "Verify carried asset provenance", draft, env)
    pypi, env = _job_directory(fake, "publish-pypi")
    shutil.copytree(draft / "github-state", pypi / "github-state")
    _step("publish-pypi", "Fetch the PyPI index", pypi, env)
    _step("publish-pypi", "Skip artefacts already on PyPI", pypi, env)
    if outputs(pypi / "output")["remaining"] == "true":
        for staged in (pypi / "dist" / "publish").iterdir():
            if (fake.pypi / staged.name).exists():
                pytest.fail("PyPI never overwrites")
            shutil.copyfile(staged, fake.pypi / staged.name)
            # `attest` attested these bytes before the PyPI upload, so the
            # release job can verify the copy PyPI now serves back.
            fake.attest_approved(fake.pypi / staged.name)


def _pinned_bundle_env(env: dict[str, str], output: pth.Path) -> dict[str, str]:
    """Return the environment the workflow gives the final destination check."""
    pinned = outputs(output)
    return {
        **env,
        "EXPECTED_BUNDLE": pinned.get("bundle") or pinned.get("verified_bundle", ""),
        "EXPECTED_DIGEST": pinned.get("bundle_digest")
        or pinned.get("verified_digest", ""),
    }


def _publish_release(
    fake: FakeRelease,
    *,
    verify: bool = True,
    env_extra: dict[str, str] | None = None,
) -> pth.Path:
    """Run ``publish-release`` through publication; return its workspace."""
    cwd, env = _job_directory(fake, "publish-release")
    env = {**env, **(env_extra or {})}
    for name in _PUBLISH_STEPS:
        _step("publish-release", name, cwd, env)
    written = outputs(cwd / "output")
    if written["files"] == "true":
        _step("publish-release", _UPLOAD_STEP, cwd, env)
    if written["pending"] == "true" or written["covered"] == "false":
        _step("publish-release", _ASSEMBLY_STEP, cwd, env)
    else:
        _step("publish-release", "Verify release distributions", cwd, env)
    if verify:
        # Mirror the workflow's env: the assembler's bundle wins, and a
        # complete re-run pins the covering bundle the coverage step verified.
        expected = _pinned_bundle_env(env, cwd / "output")
        _step(
            "publish-release",
            "Refresh the PyPI index before the final check",
            cwd,
            env,
        )
        _step(
            "publish-release",
            "Check both destinations hold the same bytes",
            cwd,
            expected,
        )
        _step("publish-release", "Publish the GitHub Release", cwd, env)
    return cwd


def _holdings(directory: pth.Path) -> dict[str, bytes]:
    """Return each file's bytes in one destination."""
    return {path.name: path.read_bytes() for path in directory.iterdir()}


def _seeded(root: pth.Path) -> FakeRelease:
    """Return destinations holding one name per reconciliation case."""
    release = fake_release(root)
    (release.pypi / _PYPI_ONLY).write_bytes(b"pypi")
    (release.github / _GITHUB_ONLY).write_bytes(b"github")
    release.attest_approved(release.github / _GITHUB_ONLY)
    (release.pypi / _BOTH).write_bytes(b"both")
    (release.github / _BOTH).write_bytes(b"both")
    release.attest_approved(release.github / _BOTH)
    return release


def _basename(path: str) -> str:
    """Return the final path component of one recorded upload argument."""
    return path.rsplit("/", 1)[-1]
