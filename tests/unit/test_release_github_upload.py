"""Tests for assembling and checking the GitHub Release upload set.

``publish-release`` stages only the names the release lacks, fetches PyPI's
bytes for names PyPI already holds, and requires approved provenance for those
fetched bytes before they may be uploaded. The attached attestation bundle is
assembled by the workflow from the store after the upload, so this module owns
only the distribution upload set.
"""

from __future__ import annotations

import json
import typing as typ

import pytest

from scripts.release_assets import PypiFile, ReleaseAssetError, State
from scripts.release_github_upload import (
    check_fetched,
    check_provenance,
    stage_github,
)
from tests.release_asset_provenance import provenance_record, sha256_hex

if typ.TYPE_CHECKING:
    from pathlib import Path

_NAME = "git_donkey-1.0.0-py3-none-any.whl"
_URL = "https://files.example.test/g.whl"


def _write(path: Path, data: bytes) -> Path:
    """Write ``data`` to ``path``, creating its parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _state(
    tmp_path: Path,
    *,
    on_pypi: bool,
    on_github: bool,
) -> State:
    """Build a state whose one local name has the requested holdings."""
    publish = tmp_path / "publish"
    _write(publish / _NAME, b"local")
    digest = sha256_hex(b"published")
    pypi = {_NAME: PypiFile(sha256=digest, url=_URL)} if on_pypi else {}
    github: dict[str, str | None] = {}
    if on_github:
        github[_NAME] = digest if on_pypi else sha256_hex(b"local")
    return State(publish, pypi, github)


def test_stage_github_returns_the_pypi_files_the_release_lacks(
    tmp_path: Path,
) -> None:
    """A name only PyPI holds is fetched from its index URL."""
    state = _state(tmp_path, on_pypi=True, on_github=False)
    output = tmp_path / "upload"

    fetch = stage_github(state, output)

    assert fetch == [(_NAME, _URL)], "the workflow must fetch PyPI's bytes"
    assert not list(output.iterdir()), (
        "the fetched file arrives after this step, not during it"
    )


def test_stage_github_stages_no_local_bytes(tmp_path: Path) -> None:
    """PyPI's bytes are canonical, so the release never takes a rebuild."""
    state = _state(tmp_path, on_pypi=True, on_github=False)
    output = tmp_path / "upload"

    stage_github(state, output)

    assert not (output / _NAME).exists(), (
        "the local rebuild must not stand in for PyPI's bytes"
    )


def test_a_published_rerun_returns_nothing(tmp_path: Path) -> None:
    """Every name published leaves the fetch list and the upload empty."""
    state = _state(tmp_path, on_pypi=True, on_github=True)
    output = tmp_path / "upload"
    output.mkdir()

    fetch = stage_github(state, output)

    assert not fetch, "nothing needs fetching"
    assert not list(output.iterdir()), "a published re-run must stage nothing"


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
        stage_github(state, tmp_path / "upload")


def test_stage_github_rejects_a_non_https_download(tmp_path: Path) -> None:
    """PyPI's bytes are never fetched over an unauthenticated transport."""
    state = _state(tmp_path, on_pypi=True, on_github=False)
    state.pypi[_NAME] = PypiFile(sha256=state.pypi[_NAME].sha256, url="http://x/g.whl")

    with pytest.raises(ReleaseAssetError, match="must use HTTPS"):
        stage_github(state, tmp_path / "upload")


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
