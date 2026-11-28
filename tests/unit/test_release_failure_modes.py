"""Failure modes of the release's asset and provenance reconciliation.

These scenarios prove the release fails closed: an untrusted or mismatching
asset, a missing subject, an unverifiable bundle, a moved tag, or an
unavailable index stops publication or leaves a published release untouched.
"""

from __future__ import annotations

import json
import shutil
import sys
import typing as typ

import pytest

from tests.release_asset_provenance import bundle_line, provenance_record, sha256_hex
from tests.release_reconciliation import (
    _ARTEFACTS,
    _ASSEMBLED_BUNDLE,
    _ASSEMBLY_STEP,
    _BOTH,
    _EXISTING_BUNDLE,
    _GITHUB_ONLY,
    _PRE_PROVENANCE,
    _PROVENANCE_STEP,
    _PYPI_ONLY,
    _draft_and_publish_pypi,
    _holdings,
    _job_directory,
    _local,
    _publish_release,
    _seeded,
    _step,
)
from tests.release_workflow import (
    AttestationOverrides,
    FakeRelease,
    fake_release,
    link_scripts,
    outputs,
    run_bash,
    step_script,
)

if typ.TYPE_CHECKING:
    import pathlib as pth
    import subprocess


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


def _ready_to_verify(
    fake: FakeRelease, *, extra: str | None = None
) -> tuple[pth.Path, dict[str, str]]:
    """Run ``publish-release`` up to, but not including, the provenance gate.

    ``extra`` names one more locally built artefact to stage, so a test can
    present more than the four fixed reconciliation cases.

    Returns
    -------
    tuple[pathlib.Path, dict[str, str]]
        The job's workspace and the environment its steps run with.
    """
    _draft_and_publish_pypi(fake)
    cwd, env = _job_directory(fake, "publish-release")
    if extra is not None:
        (cwd / "dist" / "publish" / extra).write_bytes(_local(extra))
    for name in _PRE_PROVENANCE:
        _step("publish-release", name, cwd, env)
    return cwd, env


def _no_github_upload(fake: FakeRelease) -> None:
    """Require that no asset reached the GitHub Release."""
    uploads = [call for call in fake.calls("gh") if call[:2] == ["release", "upload"]]
    assert not uploads, "a failed provenance gate must not upload anything"


def _assert_gate_refuses(
    fake: FakeRelease, cwd: pth.Path, env: dict[str, str], *, named: str = _PYPI_ONLY
) -> subprocess.CompletedProcess[str]:
    """Run the provenance gate and require it to refuse and upload nothing."""
    completed = run_bash(step_script("publish-release", _PROVENANCE_STEP), cwd, env)
    assert completed.returncode != 0, "the provenance gate must refuse"
    assert named in completed.stderr, "the failure must name the file"
    assert _PYPI_ONLY not in _holdings(fake.github), "nothing may reach the release"
    _no_github_upload(fake)
    return completed


def test_unverified_differing_asset_stops_before_pypi_upload(
    tmp_path: pth.Path,
) -> None:
    """An untrusted GitHub asset fails staging before either destination writes."""
    fake = fake_release(tmp_path)
    (fake.github / _GITHUB_ONLY).write_bytes(b"untrusted carried bytes")
    draft, env = _job_directory(fake, "draft-release")
    _step("draft-release", "Snapshot the release assets", draft, env)
    _step("draft-release", "Verify carried asset provenance", draft, env)

    pypi, env = _job_directory(fake, "publish-pypi")
    shutil.copytree(draft / "github-state", pypi / "github-state")
    _step("publish-pypi", "Fetch the PyPI index", pypi, env)
    local_bytes = _local(_GITHUB_ONLY)
    completed = run_bash(
        step_script("publish-pypi", "Skip artefacts already on PyPI"),
        pypi,
        env,
    )

    assert completed.returncode != 0, "unverified release bytes must stop staging"
    assert _GITHUB_ONLY in completed.stderr, (
        "the staging failure must name the unverified release asset"
    )
    assert "delete the named asset and rerun" in completed.stderr, (
        "the staging failure must explain the recovery action"
    )
    assert (pypi / "dist" / "publish" / _GITHUB_ONLY).read_bytes() == local_bytes, (
        "failed provenance validation must preserve this run's local bytes"
    )
    assert not (fake.pypi / _GITHUB_ONLY).exists(), "PyPI receives no bytes"


def test_reported_digest_mismatch_stops_before_pypi_upload(fake: FakeRelease) -> None:
    """Reject carried bytes that disagree with GitHub's release metadata."""
    reported_digest = sha256_hex(b"different release metadata")
    fake.reported_digests[_GITHUB_ONLY] = reported_digest
    draft, env = _job_directory(fake, "draft-release")
    _step("draft-release", "Snapshot the release assets", draft, env)
    _step("draft-release", "Verify carried asset provenance", draft, env)

    record = draft / "github-state" / "provenance" / f"{_GITHUB_ONLY}.json"
    assert json.loads(record.read_text(encoding="utf-8")) == provenance_record(
        _GITHUB_ONLY, sha256_hex(b"github")
    ), "the verifier must attest the downloaded bytes, not the metadata override"

    pypi, env = _job_directory(fake, "publish-pypi")
    shutil.copytree(draft / "github-state", pypi / "github-state")
    _step("publish-pypi", "Fetch the PyPI index", pypi, env)
    publish = pypi / "dist" / "publish"
    before_publish = _holdings(publish)
    before_pypi = _holdings(fake.pypi)
    completed = run_bash(
        step_script("publish-pypi", "Skip artefacts already on PyPI"), pypi, env
    )

    assert completed.returncode != 0, "mismatched release metadata must stop staging"
    assert _GITHUB_ONLY in completed.stderr, "the error must name the carried asset"
    assert f"expected {reported_digest}" in completed.stderr, (
        "the error must identify the independently reported digest"
    )
    assert "delete the named asset and rerun" in completed.stderr, (
        "the error must explain the recovery action"
    )
    assert _holdings(publish) == before_publish, "staging must not change local bytes"
    assert _holdings(fake.pypi) == before_pypi, "PyPI must receive no uploaded bytes"
    assert not (fake.root / "publish-release").exists(), (
        "a failed staging step must prevent the publish-release job"
    )
    assert not any(call[:2] == ["release", "upload"] for call in fake.calls("gh")), (
        "a failed staging step must not upload to the GitHub release"
    )


def test_a_starter_asset_refuses_reconciliation(tmp_path: pth.Path) -> None:
    """Reconciliation stops until an incomplete upload is removed."""
    fake = fake_release(tmp_path)
    digest = sha256_hex(b"published")
    for name in _ARTEFACTS:
        (fake.pypi / name).write_bytes(b"published")
        (fake.github / name).write_bytes(b"published")
        fake.add_attestation(fake.github / name)
    (fake.github / _ASSEMBLED_BUNDLE).write_text(
        "\n".join(bundle_line(name, digest) for name in _ARTEFACTS) + "\n",
        encoding="utf-8",
    )
    fake.asset_states[_ARTEFACTS[0]] = "starter"
    link_scripts(tmp_path)
    (tmp_path / "dist" / "publish").mkdir(parents=True)
    env = fake.env()
    listed = run_bash(
        step_script("publish-release", "List the release assets"), tmp_path, env
    )
    assert listed.returncode == 0, listed.stderr

    completed = run_bash(
        step_script("publish-release", "Check the attached bundle coverage"),
        tmp_path,
        env,
    )

    assert completed.returncode != 0, "a starter asset must stop reconciliation"
    assert "incomplete upload" in completed.stderr, (
        "the failure must identify the incomplete upload"
    )


def test_a_file_removed_from_pypi_fails_the_final_check(fake: FakeRelease) -> None:
    """The final check reads a freshly fetched index, not the staging snapshot."""
    _draft_and_publish_pypi(fake)
    cwd = _publish_release(fake, verify=False)
    (fake.pypi / _PYPI_ONLY).unlink()
    env = fake.env(GITHUB_OUTPUT=str(cwd / "output"))
    refreshed = run_bash(
        step_script("publish-release", "Refresh the PyPI index before the final check"),
        cwd,
        env,
    )
    assert refreshed.returncode == 0, refreshed.stderr

    completed = run_bash(
        step_script("publish-release", "Check both destinations hold the same bytes"),
        cwd,
        env,
    )

    assert completed.returncode != 0, "a removed PyPI file must stop publication"
    assert f"{_PYPI_ONLY} is missing from PyPI" in completed.stderr, (
        "the failure must name the file PyPI no longer serves"
    )


def test_the_final_check_accepts_the_bundle_it_verified(fake: FakeRelease) -> None:
    """The run-unique bundle this run attached satisfies the pin."""
    _draft_and_publish_pypi(fake)
    cwd = _publish_release(fake, verify=False)
    digest = sha256_hex((fake.github / _ASSEMBLED_BUNDLE).read_bytes())

    completed = run_bash(
        step_script("publish-release", "Check both destinations hold the same bytes"),
        cwd,
        fake.env(
            GITHUB_OUTPUT=str(cwd / "output"),
            EXPECTED_BUNDLE=_ASSEMBLED_BUNDLE,
            EXPECTED_DIGEST=digest,
        ),
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    ("bundle", "message"),
    [
        (_ASSEMBLED_BUNDLE, f"{_ASSEMBLED_BUNDLE} differs"),
        ("older.attestations.jsonl", "older.attestations.jsonl is missing"),
    ],
    ids=["replaced-bundle", "older-bundle-only"],
)
def test_the_final_check_rejects_another_bundle(
    fake: FakeRelease, bundle: str, message: str
) -> None:
    """A replaced or older bundle must not stand in for the verified one."""
    _draft_and_publish_pypi(fake)
    cwd = _publish_release(fake, verify=False)

    completed = run_bash(
        step_script("publish-release", "Check both destinations hold the same bytes"),
        cwd,
        fake.env(
            GITHUB_OUTPUT=str(cwd / "output"),
            EXPECTED_BUNDLE=bundle,
            EXPECTED_DIGEST="0" * 64,
        ),
    )

    assert completed.returncode != 0, "the final check must reject another bundle"
    assert message in completed.stderr, "the failure must name the bundle"


def test_the_check_fails_when_the_destinations_disagree(fake: FakeRelease) -> None:
    """Differing bytes under one name stop the release before it is visible."""
    (fake.github / _BOTH).write_bytes(b"clobbered by an earlier workflow")
    fake.attest_approved(fake.github / _BOTH)
    _draft_and_publish_pypi(fake)
    cwd = _publish_release(fake, verify=False)

    completed = run_bash(
        step_script("publish-release", "Check both destinations hold the same bytes"),
        cwd,
        fake.env(GITHUB_OUTPUT=str(cwd / "output")),
    )

    assert completed.returncode != 0, "a digest mismatch must fail the release"
    assert f"{_BOTH} differs" in completed.stderr, (
        "the failure must name the differing artefact"
    )
    assert (fake.github / _BOTH).read_bytes() == b"clobbered by an earlier workflow", (
        "the existing asset must not be overwritten"
    )


def test_an_unverifiable_attached_bundle_is_replaced(tmp_path: pth.Path) -> None:
    """A bundle that does not verify for the release is rebuilt."""
    fake = fake_release(tmp_path)
    for name in _ARTEFACTS:
        (fake.pypi / name).write_bytes(f"published {name}".encode())
        if name != _PYPI_ONLY:
            shutil.copyfile(fake.pypi / name, fake.github / name)
    fake.attest_pypi_holdings()
    (fake.github / _EXISTING_BUNDLE).write_text(
        bundle_line("wrong.whl", "0" * 64) + "\n", encoding="utf-8"
    )

    cwd = _publish_release(fake)

    written = outputs(cwd / "output")
    assert written["covered"] == "false", "the invalid bundle must not count"
    assert (fake.github / _EXISTING_BUNDLE).exists(), (
        "an unverifiable asset must not be deleted"
    )
    assert (fake.github / _PYPI_ONLY).read_bytes() == (
        f"published {_PYPI_ONLY}".encode()
    ), "PyPI's bytes must reach the release"
    assert (fake.github / _ASSEMBLED_BUNDLE).exists(), (
        "the workflow must upload a valid replacement bundle"
    )


def test_an_assembled_bundle_missing_a_subject_stops_publication(
    tmp_path: pth.Path,
) -> None:
    """The assembled bundle itself must name every distribution."""
    fake = _seeded(tmp_path)
    _draft_and_publish_pypi(fake)
    cwd, env = _job_directory(fake, "publish-release")
    for name in _PRE_PROVENANCE:
        _step("publish-release", name, cwd, env)

    completed = run_bash(
        step_script("publish-release", _ASSEMBLY_STEP),
        cwd,
        {**env, "FAKE_ATTEST_DOWNLOAD_SUBJECT": "wrong.whl"},
    )

    assert completed.returncode != 0, (
        "an assembled bundle without the released subjects must stop publication"
    )
    assert "not covered by the assembled bundle" in completed.stderr, (
        "the failure must name the missing coverage"
    )
    assert not any(call[:2] == ["release", "upload"] for call in fake.calls("gh")), (
        "nothing may be uploaded after a failed bundle verification"
    )


def test_an_unverifiable_covering_bundle_does_not_block_a_valid_one(
    tmp_path: pth.Path,
) -> None:
    """A valid covering bundle is accepted beside an unverifiable one."""
    fake = fake_release(tmp_path)
    digest = sha256_hex(b"published")
    for name in _ARTEFACTS:
        (fake.pypi / name).write_bytes(b"published")
        (fake.github / name).write_bytes(b"published")
        fake.add_attestation(fake.github / name)
    both = "\n".join(bundle_line(name, digest) for name in _ARTEFACTS) + "\n"
    (fake.github / _EXISTING_BUNDLE).write_text(both, encoding="utf-8")
    (fake.github / _ASSEMBLED_BUNDLE).write_text(both, encoding="utf-8")

    cwd = _publish_release(fake, env_extra={"FAKE_BUNDLE_FAIL_NAME": _EXISTING_BUNDLE})

    written = outputs(cwd / "output")
    assert written["covered"] == "true", "the valid covering bundle must count"
    assert written["pending"] == "false", "the release needs no new bundle"
    assert (fake.github / _EXISTING_BUNDLE).exists(), "old assets are preserved"
    assert (fake.github / _ASSEMBLED_BUNDLE).exists(), "valid assets are preserved"
    assert not any(call[:2] == ["release", "upload"] for call in fake.calls("gh")), (
        "a complete release must not upload"
    )


def test_a_publish_bundle_download_failure_changes_nothing(
    tmp_path: pth.Path,
) -> None:
    """A download failure in the publish job must not rebuild the release."""
    fake = fake_release(tmp_path)
    digest = sha256_hex(b"published")
    for name in _ARTEFACTS:
        (fake.pypi / name).write_bytes(b"published")
        (fake.github / name).write_bytes(b"published")
        fake.add_attestation(fake.github / name)
    (fake.github / _ASSEMBLED_BUNDLE).write_text(
        "\n".join(bundle_line(name, digest) for name in _ARTEFACTS) + "\n",
        encoding="utf-8",
    )
    link_scripts(tmp_path)
    (tmp_path / "dist" / "publish").mkdir(parents=True)
    env = fake.env(FAKE_DOWNLOAD_FAIL="*.attestations.jsonl")
    listed = run_bash(
        step_script("publish-release", "List the release assets"), tmp_path, env
    )
    assert listed.returncode == 0, listed.stderr

    completed = run_bash(
        step_script("publish-release", "Check the attached bundle coverage"),
        tmp_path,
        env,
    )

    assert completed.returncode != 0, "a failed bundle download must fail the step"
    assert "controlled download failure" in completed.stderr, (
        "the failure must identify the download"
    )
    assert not any(call[:2] == ["release", "upload"] for call in fake.calls("gh")), (
        "a failed download must not upload"
    )
    assert (fake.github / _ASSEMBLED_BUNDLE).exists(), "the bundle must be preserved"


def test_an_existing_asset_from_another_commit_stops_publication(
    tmp_path: pth.Path,
) -> None:
    """A moved tag cannot publish assets attested for a different commit."""
    fake = fake_release(tmp_path)
    for name in _ARTEFACTS:
        (fake.pypi / name).write_bytes(b"published")
        (fake.github / name).write_bytes(b"published")
        fake.add_attestation(
            fake.github / name, AttestationOverrides(source_digest="b" * 40)
        )
    cwd, env = _job_directory(fake, "publish-release")
    for name in _PRE_PROVENANCE:
        _step("publish-release", name, cwd, env)

    completed = run_bash(
        step_script("publish-release", _ASSEMBLY_STEP),
        cwd,
        {**env, "PENDING": "false", "COVERED": "false"},
    )

    assert completed.returncode != 0, (
        "an asset attested for another commit must stop publication"
    )
    assert "No attestations satisfy the verification policy" in completed.stderr, (
        "the failure must come from the identity policy"
    )
    assert not any(call[:2] == ["release", "upload"] for call in fake.calls("gh")), (
        "no asset may be uploaded after a failed verification"
    )


def test_bytes_that_do_not_match_the_index_are_never_uploaded(
    fake: FakeRelease,
) -> None:
    """A PyPI download whose digest differs from the index stops the upload."""
    _draft_and_publish_pypi(fake)
    cwd, env = _job_directory(fake, "publish-release")
    for name in _PRE_PROVENANCE[:3]:
        _step("publish-release", name, cwd, env)
    (fake.pypi / _PYPI_ONLY).write_bytes(b"tampered in transit")
    _step("publish-release", "Stage the assets the release lacks", cwd, env)

    completed = run_bash(
        step_script("publish-release", "Check the fetched PyPI bytes"), cwd, env
    )

    assert completed.returncode != 0, "a download must match PyPI's digest"
    assert _PYPI_ONLY in completed.stderr, "the failure must name the file"
    assert _PYPI_ONLY not in _holdings(fake.github), "the file must not be uploaded"


def test_a_fetched_file_without_provenance_is_never_uploaded(
    tmp_path: pth.Path,
) -> None:
    """The index digest alone must not place bytes on the release."""
    fake = _seeded(tmp_path)
    cwd, env = _ready_to_verify(fake)

    completed = _assert_gate_refuses(fake, cwd, env)

    assert "Could not verify build provenance" in completed.stderr, (
        "the step must report the failed verification"
    )


def test_the_index_digest_cannot_authorize_other_bytes(tmp_path: pth.Path) -> None:
    """An attestation for genuine bytes never covers malicious replacements."""
    fake = _seeded(tmp_path)
    fake.attest_approved(fake.pypi / _PYPI_ONLY)
    (fake.pypi / _PYPI_ONLY).write_bytes(b"malicious bytes matching the index")
    cwd, env = _ready_to_verify(fake)

    _assert_gate_refuses(fake, cwd, env)


@pytest.mark.parametrize(
    "overrides",
    [
        AttestationOverrides(source_ref="refs/tags/v9.9.9"),
        AttestationOverrides(source_digest="b" * 40),
        AttestationOverrides(
            signer_workflow="attacker/other/.github/workflows/release.yml"
        ),
    ],
    ids=["other-tag", "other-commit", "other-signer"],
)
def test_evidence_for_another_identity_does_not_authorize_the_bytes(
    tmp_path: pth.Path, overrides: AttestationOverrides
) -> None:
    """Only this tag, commit, and signer workflow may approve the upload."""
    fake = _seeded(tmp_path)
    fake.add_attestation(fake.pypi / _PYPI_ONLY, overrides)
    cwd, env = _ready_to_verify(fake)

    _assert_gate_refuses(fake, cwd, env)


def test_a_subject_name_mismatch_is_rejected(tmp_path: pth.Path) -> None:
    """A record must name the exact file, not merely carry its digest."""
    fake = _seeded(tmp_path)
    fake.add_attestation(
        fake.pypi / _PYPI_ONLY, AttestationOverrides(subject_name="other.whl")
    )
    cwd, env = _ready_to_verify(fake)

    completed = _assert_gate_refuses(fake, cwd, env)

    assert "has no approved build provenance" in completed.stderr, (
        "the gate must report the missing provenance"
    )


def test_a_later_unattested_file_stops_the_whole_upload(tmp_path: pth.Path) -> None:
    """One passing candidate must not let a later failing one slip through."""
    later = "git_donkey-2-py3-none-any.whl"
    fake = _seeded(tmp_path)
    fake.attest_approved(fake.pypi / _PYPI_ONLY)
    (fake.pypi / later).write_bytes(b"second pypi file")
    cwd, env = _ready_to_verify(fake, extra=later)

    _assert_gate_refuses(fake, cwd, env, named=later)


def test_every_unattested_file_is_named_in_one_run(tmp_path: pth.Path) -> None:
    """The gate reports every file it could not verify, not just the first."""
    later = "git_donkey-2-py3-none-any.whl"
    fake = _seeded(tmp_path)
    (fake.pypi / later).write_bytes(b"second pypi file")
    cwd, env = _ready_to_verify(fake, extra=later)

    completed = _assert_gate_refuses(fake, cwd, env)

    assert later in completed.stderr, "the later failure must be named too"


def test_an_old_github_cli_fails_before_any_verification(fake: FakeRelease) -> None:
    """An unpatched CLI cannot be trusted to enforce the identity flags."""
    cwd, env = _ready_to_verify(fake)
    before = len(fake.calls("gh"))

    completed = run_bash(
        step_script("publish-release", _PROVENANCE_STEP),
        cwd,
        {**env, "FAKE_GH_VERSION": "2.101.9"},
    )

    assert completed.returncode != 0, "an old CLI must fail the gate"
    assert "GitHub CLI 2.102.0 or newer is required; found 2.101.9." in (
        completed.stderr
    ), "the gate must name the minimum and the found release"
    assert not any(
        call[:2] == ["attestation", "verify"] for call in fake.calls("gh")[before:]
    ), "no attestation may be checked with an unpatched CLI"
