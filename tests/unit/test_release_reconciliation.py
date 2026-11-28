"""End-to-end behaviour of the release's canonical-byte reconciliation.

The happy-path scenarios run the checked-in steps of ``draft-release``,
``publish-pypi``, and ``publish-release`` in order against fake ``gh`` and
``curl`` destinations: every name converges on its canonical bytes, each
destination receives only what it lacks, and a complete rerun changes nothing.
"""

from __future__ import annotations

import shutil
import sys
import typing as typ

import pytest

from tests.release_asset_provenance import bundle_line, sha256_hex
from tests.release_reconciliation import (
    _ARTEFACTS,
    _ASSEMBLED_BUNDLE,
    _BOTH,
    _EXISTING_BUNDLE,
    _GITHUB_ONLY,
    _NEITHER,
    _PREDICATE_TYPE,
    _PYPI_ONLY,
    _basename,
    _draft_and_publish_pypi,
    _holdings,
    _local,
    _publish_release,
    _seeded,
)
from tests.release_workflow import (
    FakeRelease,
    fake_release,
    outputs,
)

if typ.TYPE_CHECKING:
    import pathlib as pth


@pytest.fixture
def fake(tmp_path: pth.Path) -> FakeRelease:
    """Return the seeded destinations with PyPI's bytes attested."""
    release = _seeded(tmp_path)
    release.attest_approved(release.pypi / _PYPI_ONLY)
    return release


pytestmark = [
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="The release workflow's steps run under Bash on Linux.",
    ),
    pytest.mark.timeout(120),
]


def test_pypi_downloads_retry_and_are_bounded(fake: FakeRelease) -> None:
    """Every PyPI file download keeps the retry and timeout flags."""
    _draft_and_publish_pypi(fake)
    _publish_release(fake)

    downloads = [call for call in fake.calls("curl") if "--fail" in call]
    assert downloads, "the PyPI-only name must be fetched from PyPI"
    for call in downloads:
        joined = " ".join(call)
        for flag in ("--retry 5", "--retry-all-errors", "--connect-timeout 10"):
            assert flag in joined, f"a PyPI download must pass {flag}"


def test_an_attested_pypi_file_carries_over_to_the_release(
    fake: FakeRelease,
) -> None:
    """PyPI's bytes may be uploaded when this tag and commit attested them."""
    _draft_and_publish_pypi(fake)
    cwd = _publish_release(fake)

    assert (fake.github / _PYPI_ONLY).read_bytes() == b"pypi", (
        "approved PyPI bytes must reach the release"
    )
    evidence = cwd / "pypi-provenance" / f"{_PYPI_ONLY}.json"
    assert evidence.exists(), "the verifier record must be kept for the gate"
    assert not (cwd / "github-upload" / f"{_PYPI_ONLY}.json").exists(), (
        "verifier output must stay outside the directory that is uploaded"
    )


def test_a_fresh_release_publishes_from_an_empty_github_release(
    tmp_path: pth.Path,
) -> None:
    """An empty draft gains every distribution and a bundle."""
    fake = fake_release(tmp_path)

    _draft_and_publish_pypi(fake)
    cwd = _publish_release(fake)

    written = outputs(cwd / "output")
    assert written["covered"] == "false", "an empty release has no combined bundle yet"
    assert written["files"] == "true", "every distribution must be staged"
    assert written["pending"] == "true", "the release needs a bundle"
    assert set(_holdings(fake.github)) == {*_ARTEFACTS, _ASSEMBLED_BUNDLE}, (
        "the fresh release must hold every distribution and its bundle"
    )


def test_a_pypi_only_name_uploads_when_the_bundle_is_already_attached(
    fake: FakeRelease,
) -> None:
    """A re-run of ``publish-release`` alone still fetches PyPI's missing bytes."""
    for name in _ARTEFACTS:
        (fake.pypi / name).write_bytes(f"published {name}".encode())
        if name != _PYPI_ONLY:
            shutil.copyfile(fake.pypi / name, fake.github / name)
    fake.attest_pypi_holdings()
    existing = [name for name in _ARTEFACTS if name != _PYPI_ONLY]
    lines = [
        bundle_line(name, sha256_hex(f"published {name}".encode())) for name in existing
    ]
    (fake.github / _EXISTING_BUNDLE).write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )

    cwd = _publish_release(fake)

    written = outputs(cwd / "output")
    assert written["files"] == "true", "the missing release name must be staged"
    assert written["pending"] == "true", "the missing name needs an upload"
    assert written["covered"] == "true", (
        "the existing bundle must cover the assets already on the release"
    )
    assert (
        fake.github / _PYPI_ONLY
    ).read_bytes() == f"published {_PYPI_ONLY}".encode(), (
        "PyPI's bytes must reach the release"
    )


def test_a_rerun_ignores_an_older_partial_bundle(tmp_path: pth.Path) -> None:
    """A complete bundle is verified without failing on an older partial one."""
    fake = fake_release(tmp_path)
    digest = sha256_hex(b"published")
    existing = ("git_donkey-1-py3-none-any.whl", _NEITHER)
    for name in _ARTEFACTS:
        (fake.pypi / name).write_bytes(b"published")
        (fake.github / name).write_bytes(b"published")
        fake.add_attestation(fake.github / name)
    (fake.github / _EXISTING_BUNDLE).write_text(
        bundle_line(existing[0], digest) + "\n", encoding="utf-8"
    )
    (fake.github / _ASSEMBLED_BUNDLE).write_text(
        "\n".join(bundle_line(name, digest) for name in _ARTEFACTS) + "\n",
        encoding="utf-8",
    )

    cwd = _publish_release(fake)

    written = outputs(cwd / "output")
    assert written["files"] == "false", "the release needs no distributions"
    assert written["pending"] == "false", "the release needs no bundle"
    assert written["covered"] == "true", "the complete bundle covers the release"
    assert not any(call[:2] == ["release", "upload"] for call in fake.calls("gh")), (
        "a complete re-run must not upload"
    )
    assert not any(
        call[:2] == ["attestation", "download"] for call in fake.calls("gh")
    ), "a complete re-run must not download attestations"


def test_verified_prior_run_asset_is_published_to_pypi(fake: FakeRelease) -> None:
    """A tag-bound prior attestation permits interrupted-run carryover."""
    _draft_and_publish_pypi(fake)

    assert (fake.pypi / _GITHUB_ONLY).read_bytes() == b"github", (
        "PyPI must receive the bytes from the approved prior release"
    )
    assert any(call[:2] == ["attestation", "verify"] for call in fake.calls("gh")), (
        "the draft job must verify the carried asset before PyPI staging"
    )


def test_each_destination_is_sent_only_what_it_lacks(fake: FakeRelease) -> None:
    """GitHub is sent the PyPI-only and new names and the bundle, nothing else."""
    _draft_and_publish_pypi(fake)
    _publish_release(fake)

    uploads = [call for call in fake.calls("gh") if call[:2] == ["release", "upload"]]
    assert uploads, "the release must receive the names it lacks"
    sent = sorted(_basename(path) for call in uploads for path in call[3:])
    assert sent == sorted((_PYPI_ONLY, _NEITHER, _ASSEMBLED_BUNDLE)), (
        "the release must receive only the names it lacks plus its bundle"
    )
    assert not any("--clobber" in call for call in fake.calls("gh")), (
        "no upload may overwrite an existing asset"
    )


def test_a_rerun_after_success_uploads_nothing(fake: FakeRelease) -> None:
    """A second complete run finds every name on both sides and changes nothing."""
    _draft_and_publish_pypi(fake)
    _publish_release(fake)
    before = (_holdings(fake.pypi), _holdings(fake.github))
    for job in ("draft-release", "publish-pypi", "publish-release"):
        shutil.rmtree(fake.root / job)
    (fake.root / "calls.jsonl").unlink(missing_ok=True)

    _draft_and_publish_pypi(fake)
    cwd = _publish_release(fake)

    assert (_holdings(fake.pypi), _holdings(fake.github)) == before, (
        "a fully published re-run must not change either destination"
    )
    assert outputs(fake.root / "publish-pypi" / "output")["remaining"] == "false", (
        "PyPI must need nothing"
    )
    written = outputs(cwd / "output")
    assert written["files"] == "false", "the release must need no distributions"
    assert written["pending"] == "false", "the release must need no bundle"
    assert written["covered"] == "true", "the attached bundle must cover every file"
    assert not any(
        call[:2] == ["attestation", "download"] for call in fake.calls("gh")
    ), "a complete re-run must not download attestations"
    assert not any(call[:2] == ["release", "upload"] for call in fake.calls("gh")), (
        "a fully published re-run must not upload"
    )
    assert not any(call[:2] == ["release", "edit"] for call in fake.calls("gh")), (
        "a fully published re-run must not edit the release"
    )


def test_the_verifier_is_called_with_the_full_identity_policy(
    fake: FakeRelease,
) -> None:
    """Every identity flag the policy depends on must reach the verifier."""
    _draft_and_publish_pypi(fake)
    _publish_release(fake)
    env = fake.env()
    verification = next(
        call
        for call in fake.calls("gh")
        if call[:2] == ["attestation", "verify"]
        and call[2].startswith("github-upload/")
    )

    expected = [
        "attestation",
        "verify",
        f"github-upload/{_PYPI_ONLY}",
        "--repo",
        env["GITHUB_REPOSITORY"],
        "--signer-workflow",
        f"{env['GITHUB_REPOSITORY']}/.github/workflows/release.yml",
        "--source-ref",
        env["GITHUB_REF"],
        "--source-digest",
        env["GITHUB_SHA"],
        "--predicate-type",
        _PREDICATE_TYPE,
        "--deny-self-hosted-runners",
        "--format",
        "json",
    ]
    assert verification == expected, (
        "the verifier must receive exactly the release policy's identity"
    )


def test_every_name_converges_on_its_canonical_bytes(fake: FakeRelease) -> None:
    """PyPI's bytes win, then GitHub's, then this run's, on both sides."""
    _draft_and_publish_pypi(fake)
    _publish_release(fake)

    expected = {
        _PYPI_ONLY: b"pypi",
        _GITHUB_ONLY: b"github",
        _NEITHER: _local(_NEITHER),
        _BOTH: b"both",
    }
    published = _holdings(fake.pypi)
    released = _holdings(fake.github)
    assert published == expected, "PyPI must hold each name's canonical bytes"
    assert {name: released[name] for name in expected} == expected, (
        "the release must hold the same bytes as PyPI"
    )
    assert _ASSEMBLED_BUNDLE in released, (
        "the release must carry the assembled attestation bundle"
    )
