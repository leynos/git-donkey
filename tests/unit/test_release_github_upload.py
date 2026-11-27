"""Tests for assembling and checking the GitHub Release upload set.

``publish-release`` stages only the names the release lacks, fetches PyPI's
bytes for names PyPI already holds, and requires approved provenance for those
fetched bytes before they may be uploaded. A fully published re-run stages
nothing at all, including no new bundle.
"""

from __future__ import annotations

import json
import typing as typ

import pytest

from scripts.release_assets import BUNDLE_SUFFIX, PypiFile, ReleaseAssetError, State
from scripts.release_github_upload import (
    check_fetched,
    check_provenance,
    stage_github,
)
from tests.release_asset_provenance import provenance_record, sha256_hex

if typ.TYPE_CHECKING:
    from pathlib import Path

_NAME = "git_donkey-1.0.0-py3-none-any.whl"
_BUNDLE = f"git-donkey-v1.0.0-run1-1{BUNDLE_SUFFIX}"
_URL = "https://files.example.test/g.whl"


def _write(path: Path, data: bytes) -> Path:
    """Write ``data`` to ``path``, creating its parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _bundles(tmp_path: Path) -> Path:
    """Create the run's attestation directory with one bundle."""
    bundles = tmp_path / "attestations"
    _write(bundles / _BUNDLE, b"bundle")
    return bundles


def _state(
    tmp_path: Path,
    *,
    on_pypi: bool,
    on_github: bool,
    github_bundle: bool = False,
) -> State:
    """Build a state whose one local name has the requested holdings."""
    publish = tmp_path / "publish"
    _write(publish / _NAME, b"local")
    digest = sha256_hex(b"published")
    pypi = {_NAME: PypiFile(sha256=digest, url=_URL)} if on_pypi else {}
    github: dict[str, str | None] = {}
    if on_github:
        github[_NAME] = digest if on_pypi else sha256_hex(b"local")
    if github_bundle:
        github[_BUNDLE] = None
    return State(publish, pypi, github)


def test_stage_github_rejects_a_name_the_index_does_not_list(
    tmp_path: Path,
) -> None:
    """PyPI's index must list every name before the release is staged.

    Uploading this run's rebuild while the index omits the name could give
    the release different bytes from the ones PyPI serves, so the step fails
    and a rerun waits for the index instead.
    """
    state = _state(tmp_path, on_pypi=False, on_github=False)

    with pytest.raises(ReleaseAssetError, match="does not list"):
        stage_github(state, _bundles(tmp_path), tmp_path / "upload")


def test_stage_github_fetches_pypi_bytes_for_a_missing_release_name(
    tmp_path: Path,
) -> None:
    """A name only PyPI holds is fetched from its index URL."""
    state = _state(tmp_path, on_pypi=True, on_github=False)
    output = tmp_path / "upload"

    fetch = stage_github(state, _bundles(tmp_path), output)

    assert fetch == [(_NAME, _URL)], "the workflow must fetch PyPI's bytes"
    assert not (output / _NAME).exists(), (
        "the fetched file arrives after this step, not during it"
    )


def test_stage_github_rejects_a_non_https_download(tmp_path: Path) -> None:
    """PyPI's bytes are never fetched over an unauthenticated transport."""
    state = _state(tmp_path, on_pypi=True, on_github=False)
    state.pypi[_NAME] = PypiFile(sha256=state.pypi[_NAME].sha256, url="http://x/g.whl")

    with pytest.raises(ReleaseAssetError, match="must use HTTPS"):
        stage_github(state, _bundles(tmp_path), tmp_path / "upload")


def test_a_published_rerun_stages_nothing(tmp_path: Path) -> None:
    """Every name published and a bundle attached leaves the upload empty."""
    state = _state(tmp_path, on_pypi=True, on_github=True, github_bundle=True)
    output = tmp_path / "upload"
    output.mkdir()

    fetch = stage_github(state, _bundles(tmp_path), output)

    assert not fetch, "nothing needs fetching"
    assert not list(output.iterdir()), (
        "a published re-run must upload neither artefacts nor a new bundle"
    )


def test_a_missing_bundle_is_staged_even_when_every_name_is_published(
    tmp_path: Path,
) -> None:
    """A release that lost its bundle regains one without touching the names."""
    state = _state(tmp_path, on_pypi=True, on_github=True)
    output = tmp_path / "upload"

    stage_github(state, _bundles(tmp_path), output)

    assert (output / _BUNDLE).is_file(), "the release must regain a bundle"
    assert not (output / _NAME).exists(), "published names must not be uploaded again"


def test_check_fetched_accepts_bytes_matching_pypi(tmp_path: Path) -> None:
    """The download must hash to the digest the index reported."""
    state = _state(tmp_path, on_pypi=True, on_github=False)
    output = tmp_path / "upload"
    _write(output / _NAME, b"published")

    check_fetched(state, output)


def test_check_fetched_rejects_other_bytes(tmp_path: Path) -> None:
    """Tampered or truncated bytes stop the upload."""
    state = _state(tmp_path, on_pypi=True, on_github=False)
    output = tmp_path / "upload"
    _write(output / _NAME, b"tampered")

    with pytest.raises(ReleaseAssetError, match="has sha256"):
        check_fetched(state, output)


def test_check_provenance_accepts_an_approved_record(tmp_path: Path) -> None:
    """A record binding the exact staged filename and digest authorizes it."""
    state = _state(tmp_path, on_pypi=True, on_github=False)
    output = tmp_path / "upload"
    _write(output / _NAME, b"published")
    evidence = tmp_path / "provenance"
    _write(
        evidence / f"{_NAME}.json",
        json.dumps(provenance_record(_NAME, sha256_hex(b"published"))).encode(),
    )

    check_provenance(state, output, evidence)


def test_check_provenance_names_every_unverified_file(tmp_path: Path) -> None:
    """One run reports all unattested files, not only the first."""
    state = _state(tmp_path, on_pypi=True, on_github=False)
    output = tmp_path / "upload"
    _write(output / _NAME, b"published")
    _write(state.publish / "second.whl", b"second")
    _write(output / "second.whl", b"second")
    state.pypi["second.whl"] = PypiFile(sha256=sha256_hex(b"second"), url=_URL)

    with pytest.raises(ReleaseAssetError, match=r"second\.whl") as raised:
        check_provenance(state, output, tmp_path / "provenance")

    assert _NAME in str(raised.value), "the first failure must be named too"
