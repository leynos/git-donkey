"""Tests for the release-asset reconciliation decisions.

The library chooses one canonical source per filename — PyPI, then the GitHub
Release, then this run — and decides exactly which names each destination
still needs. These tests pin that precedence, the digest checks, and the
fail-closed behaviour before either destination is changed.
"""

from __future__ import annotations

import json
import typing as typ
import urllib.parse

import pytest

from scripts import release_assets
from scripts.release_assets import (
    ATTESTATIONS_SUFFIX,
    BUNDLE_SUFFIX,
    PypiFile,
    ReleaseAssetError,
    Source,
    State,
    carryover,
    destination_disagreements,
    expected_bundle_problems,
    has_bundle,
    incomplete_github_assets,
    is_bundle,
    local_names,
    mismatches,
    plan,
    read_github_assets,
    read_pypi_index,
    require_digest,
    stage_pypi,
    verify,
)
from tests.release_asset_provenance import provenance_record, sha256_hex

if typ.TYPE_CHECKING:
    from pathlib import Path

_NAME = "git_donkey-1.0.0-py3-none-any.whl"
_SDIST = "git_donkey-1.0.0.tar.gz"


def _write(path: Path, data: bytes) -> Path:
    """Write ``data`` to ``path``, creating its parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _pypi_file(
    content: str, *, url: str = "https://files.example.test/g.whl"
) -> PypiFile:
    """Describe a PyPI file by the digest of its supplied content."""
    return PypiFile(sha256=sha256_hex(content.encode()), url=url)


def _state(
    tmp_path: Path,
    *,
    local: dict[str, str] | None = None,
    pypi: dict[str, str] | None = None,
    github: dict[str, str | None] | None = None,
) -> State:
    """Build a release state from literal name-to-content maps."""
    publish = tmp_path / "publish"
    publish.mkdir()
    for name, content in (local or {_NAME: "local"}).items():
        _write(publish / name, content.encode())
    pypi_files = {name: _pypi_file(content) for name, content in (pypi or {}).items()}
    github_digests = {
        name: digest
        if digest is None
        else sha256_hex((publish / name).read_bytes())
        if digest == "self"
        else digest
        for name, digest in (github or {}).items()
    }
    return State(publish, pypi_files, github_digests)


@pytest.mark.parametrize(
    ("on_pypi", "on_github", "canonical"),
    [
        (False, False, Source.LOCAL),
        (True, False, Source.PYPI),
        (False, True, Source.GITHUB),
        (True, True, Source.PYPI),
    ],
    ids=["local-only", "pypi-only", "github-only", "both"],
)
def test_plan_prefers_pypi_then_github_then_local(
    *, on_pypi: bool, on_github: bool, canonical: Source
) -> None:
    """Each destination receives only the names it lacks."""
    decided = plan(
        [_NAME],
        {_NAME} if on_pypi else set(),
        {_NAME} if on_github else set(),
    )

    assert decided.canonical[_NAME] is canonical, (
        "the canonical source must follow PyPI, GitHub, local precedence"
    )
    assert bool(decided.pypi_uploads) is not on_pypi, (
        "PyPI must receive exactly the names it lacks"
    )
    assert bool(decided.github_uploads) is not on_github, (
        "GitHub must receive exactly the names it lacks"
    )


def test_mismatches_describe_each_destination_gap() -> None:
    """Every missing or differing name produces one actionable message."""
    problems = mismatches(
        [_NAME, _SDIST, "other.whl"],
        {_NAME: "aa", _SDIST: "bb", "other.whl": "cc"},
        {_NAME: "aa", _SDIST: None, "other.whl": "dd"},
    )

    assert problems == [
        f"{_SDIST} is missing from the GitHub Release or has no digest",
        "other.whl differs: PyPI sha256 cc, GitHub sha256 dd",
    ], "each destination gap must be named"


def test_mismatches_are_empty_when_the_destinations_agree() -> None:
    """Identical digests satisfy the reconciliation check."""
    assert not mismatches([_NAME], {_NAME: "aa"}, {_NAME: "aa"}), (
        "identical digests must not be reported"
    )


def test_read_pypi_index_maps_digests_and_download_urls(tmp_path: Path) -> None:
    """The index digest is lowercased and relative URLs resolve against it."""
    index = tmp_path / "index.json"
    index.write_text(
        json.dumps({
            "files": [
                {
                    "filename": _NAME,
                    "url": "packages/g.whl",
                    "hashes": {"sha256": "AA"},
                },
                {
                    "filename": _SDIST,
                    "url": "https://files.example.test/g.tar.gz",
                    "hashes": {"sha256": "BB"},
                },
            ]
        }),
        encoding="utf-8",
    )

    files = read_pypi_index(index)

    assert files[_NAME].sha256 == "aa", "the digest must be lowercased"
    assert files[_NAME].url == urllib.parse.urljoin(
        release_assets.INDEX_URL, "packages/g.whl"
    ), "a relative URL must resolve against the index"
    assert files[_SDIST].url == "https://files.example.test/g.tar.gz", (
        "an absolute URL must be preserved"
    )


@pytest.mark.parametrize(
    ("digest", "expected"),
    [
        ("sha256:ABC", "abc"),
        (None, None),
        ("md5:abc", None),
        ("sha256:", ""),
    ],
)
def test_read_github_assets_parses_only_sha256_digests(
    tmp_path: Path, digest: str | None, expected: str | None
) -> None:
    """Only a ``sha256:`` digest establishes a comparable value."""
    assets = tmp_path / "assets.json"
    assets.write_text(
        json.dumps({"assets": [{"name": _NAME, "digest": digest}]}),
        encoding="utf-8",
    )

    assert read_github_assets(assets) == {_NAME: expected}, (
        "the release digest must be parsed from the sha256 prefix only"
    )


@pytest.mark.parametrize(
    ("state", "is_included"),
    [("uploaded", True), (None, True), ("starter", False)],
    ids=["uploaded", "state-absent", "starter"],
)
def test_read_github_assets_lists_only_completed_uploads(
    tmp_path: Path, state: str | None, *, is_included: bool
) -> None:
    """A failed transfer left as ``starter`` must not count as held."""
    assets = tmp_path / "assets.json"
    asset: dict[str, object] = {"name": _NAME, "digest": "sha256:aa"}
    if state is not None:
        asset["state"] = state
    assets.write_text(json.dumps({"assets": [asset]}), encoding="utf-8")

    expected = {_NAME: "aa"} if is_included else {}
    assert read_github_assets(assets) == expected, (
        "only a completed upload may be read as held"
    )


def test_incomplete_github_assets_names_a_starter_upload(tmp_path: Path) -> None:
    """An incomplete upload is reported by name for the refusal path."""
    assets = tmp_path / "assets.json"
    assets.write_text(
        json.dumps({"assets": [{"name": _NAME, "state": "starter"}]}),
        encoding="utf-8",
    )

    assert incomplete_github_assets(assets) == [_NAME], (
        "the incomplete upload must be named"
    )


def test_incomplete_github_assets_ignores_completed_uploads(tmp_path: Path) -> None:
    """A completed upload is not an incomplete upload."""
    assets = tmp_path / "assets.json"
    assets.write_text(
        json.dumps({"assets": [{"name": _NAME, "state": "uploaded"}]}),
        encoding="utf-8",
    )

    assert incomplete_github_assets(assets) == [], (
        "a completed upload must not be reported"
    )


def test_unsafe_artefact_names_are_refused(tmp_path: Path) -> None:
    """A name that is unsafe as a path or TSV field stops the release."""
    publish = tmp_path / "publish"
    publish.mkdir()
    (publish / "-escape.whl").write_text("", encoding="utf-8")

    with pytest.raises(ReleaseAssetError, match="refusing unsafe artefact names"):
        local_names(publish)


def test_require_digest_rejects_a_missing_expected_digest(tmp_path: Path) -> None:
    """A destination that reports no digest establishes nothing."""
    artefact = _write(tmp_path / _NAME, b"bytes")

    with pytest.raises(ReleaseAssetError, match="expected None"):
        require_digest(artefact, None)


def test_require_digest_rejects_different_bytes(tmp_path: Path) -> None:
    """The file's bytes must hash to the reported digest."""
    artefact = _write(tmp_path / _NAME, b"bytes")

    with pytest.raises(ReleaseAssetError, match="has sha256"):
        require_digest(artefact, "0" * 64)


def test_carryover_is_the_shared_name_set(tmp_path: Path) -> None:
    """Only names both this run and the release hold can be carried."""
    state = _state(
        tmp_path, local={_NAME: "local", _SDIST: "sdist"}, github={_NAME: "self"}
    )

    assert carryover(state) == [_NAME], (
        "the carryover list must be the intersection of both name sets"
    )


def test_destination_disagreements_reports_only_shared_differing_names() -> None:
    """A missing name is staged, but a shared unknown name stops staging."""
    problems = destination_disagreements(
        [_NAME, _SDIST, "other.whl", "unknown.whl"],
        {_NAME: "aa", _SDIST: "bb", "unknown.whl": "cc"},
        {_NAME: "aa", _SDIST: "cc", "other.whl": None, "unknown.whl": None},
    )

    assert problems == [
        f"{_SDIST} differs: PyPI sha256 bb, GitHub sha256 cc",
        "unknown.whl has no SHA-256 digest on the GitHub Release",
    ], "a differing or unresolvable shared name must be reported"


def test_stage_pypi_stops_when_a_published_name_differs_on_github(
    tmp_path: Path,
) -> None:
    """An upload must not begin while the destinations already disagree."""
    state = _state(
        tmp_path,
        local={_NAME: "local", _SDIST: "sdist"},
        pypi={_SDIST: "published"},
        github={_SDIST: "0" * 64},
    )

    with pytest.raises(ReleaseAssetError, match=f"{_SDIST} differs"):
        stage_pypi(state, tmp_path / "github-files", tmp_path / "provenance")

    assert (state.publish / _NAME).exists(), (
        "refusing before staging must preserve this run's artefacts"
    )
    assert (state.publish / _SDIST).exists(), "no published name may be dropped"


def test_stage_pypi_stops_when_a_shared_name_has_no_github_digest(
    tmp_path: Path,
) -> None:
    """An unknown GitHub digest cannot be reconciled before an upload."""
    state = _state(
        tmp_path,
        local={_NAME: "local"},
        pypi={_NAME: "published"},
        github={_NAME: None},
    )

    with pytest.raises(ReleaseAssetError, match="has no SHA-256 digest"):
        stage_pypi(state, tmp_path / "github-files", tmp_path / "provenance")

    assert (state.publish / _NAME).exists(), (
        "refusing before staging must preserve this run's artefacts"
    )


def test_stage_pypi_drops_names_pypi_already_holds(tmp_path: Path) -> None:
    """A published filename is never uploaded a second time."""
    state = _state(tmp_path, pypi={_NAME: "published"})

    staged = stage_pypi(state, tmp_path / "github-files", tmp_path / "provenance")

    assert not staged.remaining, "nothing remains when PyPI holds every name"
    assert staged.notices == (
        f"Skipping {_NAME}: already on PyPI",
        "Every artefact is already on PyPI; nothing to publish.",
    ), "the decision must be reported as a notice"
    assert not (state.publish / _NAME).exists(), "the published name must be removed"


def test_stage_pypi_keeps_a_name_pypi_lacks(tmp_path: Path) -> None:
    """A local build uploads when neither destination holds the name."""
    state = _state(tmp_path)

    staged = stage_pypi(state, tmp_path / "github-files", tmp_path / "provenance")

    assert staged.remaining, "an unpublished name must remain staged"
    assert (state.publish / _NAME).read_bytes() == b"local", (
        "the local attested bytes must remain for upload"
    )


def test_stage_pypi_carries_verified_github_bytes(tmp_path: Path) -> None:
    """A differing GitHub asset replaces the rebuild only when verified."""
    state = _state(tmp_path, local={_NAME: "rebuild"}, github={_NAME: "self"})
    carried = _write(tmp_path / "github-files" / _NAME, b"prior release")
    digest = sha256_hex(carried.read_bytes())
    state.github[_NAME] = digest
    record = tmp_path / "provenance" / f"{_NAME}.json"
    record.parent.mkdir()
    record.write_text(json.dumps(provenance_record(_NAME, digest)), encoding="utf-8")

    staged = stage_pypi(state, tmp_path / "github-files", tmp_path / "provenance")

    assert staged.remaining, "the carried name still needs an upload"
    assert staged.notices == (
        f"Uploading verified GitHub Release bytes for {_NAME}",
    ), "the carried upload must be reported"
    assert (state.publish / _NAME).read_bytes() == b"prior release", (
        "PyPI must receive the verified GitHub bytes"
    )


def test_stage_pypi_keeps_local_bytes_that_match_github(tmp_path: Path) -> None:
    """Byte-identical rebuilt bytes need no carryover or provenance record."""
    state = _state(tmp_path, local={_NAME: "same"}, github={_NAME: "self"})

    staged = stage_pypi(state, tmp_path / "github-files", tmp_path / "provenance")

    assert staged.remaining, "the name still needs its first PyPI upload"
    assert (state.publish / _NAME).read_bytes() == b"same", (
        "identical local bytes must be retained"
    )


def test_stage_pypi_rejects_unverified_carried_bytes(tmp_path: Path) -> None:
    """An untrusted differing asset stops staging before any mutation."""
    state = _state(tmp_path, local={_NAME: "rebuild"}, github={_NAME: "self"})
    carried = _write(tmp_path / "github-files" / _NAME, b"prior release")
    state.github[_NAME] = sha256_hex(carried.read_bytes())

    with pytest.raises(ReleaseAssetError, match="cannot safely carry GitHub asset"):
        stage_pypi(state, tmp_path / "github-files", tmp_path / "provenance")

    assert (state.publish / _NAME).read_bytes() == b"rebuild", (
        "a failed carryover must preserve the local bytes"
    )


def test_stage_pypi_rejects_a_reported_digest_mismatch(tmp_path: Path) -> None:
    """Downloaded bytes must match the digest the release API reports."""
    state = _state(tmp_path, local={_NAME: "rebuild"})
    state.github[_NAME] = "0" * 64
    carried = _write(tmp_path / "github-files" / _NAME, b"prior release")
    record = tmp_path / "provenance" / f"{_NAME}.json"
    record.parent.mkdir()
    record.write_text(json.dumps(provenance_record(_NAME, "0" * 64)), "utf-8")
    assert carried.read_bytes() == b"prior release", "the fixture must stage bytes"

    with pytest.raises(ReleaseAssetError, match="expected " + "0" * 64):
        stage_pypi(state, tmp_path / "github-files", tmp_path / "provenance")


def test_verify_requires_agreeing_digests_and_an_attached_bundle(
    tmp_path: Path,
) -> None:
    """A differing digest and a missing bundle are both reported."""
    state = _state(
        tmp_path,
        local={_NAME: "local", _SDIST: "sdist"},
        pypi={_NAME: "published", _SDIST: "published"},
        github={_SDIST: "0" * 64},
    )
    state.github[_NAME] = state.pypi[_NAME].sha256
    staged = tmp_path / "upload"
    staged.mkdir()

    problems = verify(state, staged)

    expected = (
        f"{_SDIST} differs: PyPI sha256 {state.pypi[_SDIST].sha256}, "
        f"GitHub sha256 {'0' * 64}"
    )
    assert problems == [
        expected,
        "the GitHub Release holds no provenance bundle",
    ], "the differing digest and the missing bundle must both be reported"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (f"git-donkey-v1.0.0-run1-1{BUNDLE_SUFFIX}", True),
        (f"git-donkey-v1.0.0-run1-1{ATTESTATIONS_SUFFIX}", True),
        (_NAME, False),
        ("git-donkey-v1.0.0.sigstore.txt", False),
    ],
    ids=["run-bundle", "combined-bundle", "distribution", "other-suffix"],
)
def test_bundle_names_cover_both_formats(name: str, *, expected: bool) -> None:
    """A single-run bundle and the combined attestations both count."""
    assert is_bundle(name) is expected, (
        "the release must recognise both provenance bundle formats"
    )


def test_has_bundle_detects_a_combined_bundle() -> None:
    """Only the combined attestations complete a release."""
    assert has_bundle({_NAME: None, f"g{ATTESTATIONS_SUFFIX}": None}), (
        "the combined attestation bundle must satisfy the release check"
    )
    assert not has_bundle({_NAME: None}), "a release without a bundle is incomplete"
    assert not has_bundle({_NAME: None, f"g{BUNDLE_SUFFIX}": None}), (
        "a single-run bundle cannot cover every published distribution"
    )


def test_verify_accepts_agreeing_destinations_with_a_bundle(tmp_path: Path) -> None:
    """Both destinations holding the same bytes and a bundle is clear."""
    bundle = f"git-donkey-v1.0.0-run1-1{ATTESTATIONS_SUFFIX}"
    state = _state(
        tmp_path,
        local={_NAME: "local"},
        pypi={_NAME: "published"},
        github={_NAME: "self", bundle: "ignored"},
    )
    state.github[_NAME] = state.pypi[_NAME].sha256
    staged = tmp_path / "upload"
    staged.mkdir()

    assert not verify(state, staged), "agreeing destinations with a bundle must pass"


@pytest.mark.parametrize(
    ("github", "digest", "expected"),
    [
        ({"bundle": "ab"}, "ab", []),
        (
            {"bundle": "cd"},
            "ab",
            ["bundle differs: expected sha256 ab, GitHub sha256 cd"],
        ),
        (
            {},
            "ab",
            ["bundle is missing from the GitHub Release or has no digest"],
        ),
        (
            {"bundle": "ab"},
            None,
            ["no digest was recorded for the verified bundle bundle"],
        ),
    ],
    ids=["agreeing", "differing", "missing", "digest-not-recorded"],
)
def test_expected_bundle_problems_pin_the_verified_bundle(
    github: dict[str, str | None], digest: str | None, expected: list[str]
) -> None:
    """Only the bundle this run verified may stand in at publication."""
    assert expected_bundle_problems(github, "bundle", digest) == expected, (
        "the verified bundle's name and digest must still match the release"
    )


def test_verify_rejects_a_release_distribution_outside_the_build(
    tmp_path: Path,
) -> None:
    """A distribution this run did not build cannot go unexamined."""
    state = _state(
        tmp_path,
        local={_NAME: "local"},
        pypi={_NAME: "published"},
        github={_NAME: "self", "extra.whl": "ff"},
    )
    state.github[_NAME] = state.pypi[_NAME].sha256
    staged = tmp_path / "upload"
    staged.mkdir()

    problems = verify(state, staged)

    assert (
        "extra.whl is on the GitHub Release but not part of this run's build"
        in problems
    ), "an unexpected release distribution must fail the check"


def test_verify_requires_a_staged_bundle_to_be_attached(tmp_path: Path) -> None:
    """A bundle this run uploaded must be present on the release."""
    bundle = f"git-donkey-v1.0.0-run2-1{BUNDLE_SUFFIX}"
    state = _state(
        tmp_path,
        local={_NAME: "local"},
        pypi={_NAME: "published"},
        github={_NAME: "self"},
    )
    state.github[_NAME] = state.pypi[_NAME].sha256
    staged = tmp_path / "upload"
    _write(staged / bundle, b"bundle")

    problems = verify(state, staged)

    assert f"{bundle} is missing from the GitHub Release" in problems, (
        "a staged bundle that never attached must fail the check"
    )
