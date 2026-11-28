"""Behaviour of the release workflow's GitHub Release steps.

Each step runs its checked-in script against a ``gh`` stand-in that records
every call, so the assertions are about what the release asks GitHub to do: a
re-run reuses the tag's release instead of creating a second, a new release is
drafted and marked pre-release from the version check, the draft's existing
assets are snapshotted for the PyPI job, and the release is made visible only
when its draft or pre-release state differs.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import typing as typ

import pytest

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

_TAG = "v1.2.3"
_CARRIED_ASSET = "git_donkey-1.tar.gz"
_SNAPSHOT_ASSETS = 3
_VERIFY_ATTEMPTS = 3
_TRANSIENT_ATTEMPTS = 2
_UNSUPPORTED_STATUS = 2

pytestmark = [
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="The release workflow's steps run under Bash on Linux.",
    ),
    # These tests execute checked-in shell steps, which start their own
    # processes; allow for a loaded xdist worker.
    pytest.mark.timeout(120),
]


def test_an_existing_release_is_reused(tmp_path: pth.Path) -> None:
    """A re-run finds the tag's release and creates nothing."""
    fake = fake_release(tmp_path)

    completed = run_bash(
        step_script("draft-release", "Create or reuse the draft release"),
        tmp_path,
        fake.env(IS_PRERELEASE="false"),
    )

    assert completed.returncode == 0, completed.stderr
    assert not any(call[:2] == ["release", "create"] for call in fake.calls("gh")), (
        "an existing release must be reused"
    )


@pytest.mark.parametrize(
    ("is_prerelease", "expect_prerelease_flag"),
    [("true", True), ("false", False)],
    ids=["prerelease", "final"],
)
def test_a_missing_release_is_created_as_a_draft(
    tmp_path: pth.Path, is_prerelease: str, *, expect_prerelease_flag: bool
) -> None:
    """A first run drafts the release and carries the pre-release status."""
    fake = fake_release(tmp_path)
    fake.remove_release()

    completed = run_bash(
        step_script("draft-release", "Create or reuse the draft release"),
        tmp_path,
        fake.env(IS_PRERELEASE=is_prerelease),
    )

    creates = [call for call in fake.calls("gh") if call[:2] == ["release", "create"]]
    assert completed.returncode == 0, completed.stderr
    assert len(creates) == 1, f"expected one create call, got {creates}"
    create = creates[0]
    assert create[:3] == ["release", "create", _TAG], create
    assert {"--draft", "--verify-tag", "--generate-notes"} <= set(create), (
        "the release must start as a draft of the pushed tag"
    )
    assert ("--prerelease" in create) is expect_prerelease_flag, (
        "the pre-release flag must follow the version check"
    )
    assert fake.release_state()["isDraft"] is True, "the new release must stay a draft"


def _expected_verifier_call() -> list[str]:
    """Return the verifier invocation the release identity policy requires."""
    return [
        "attestation",
        "verify",
        f"github-state/files/{_CARRIED_ASSET}",
        "--repo",
        "leynos/git-donkey",
        "--signer-workflow",
        "leynos/git-donkey/.github/workflows/release.yml",
        "--source-ref",
        f"refs/tags/{_TAG}",
        "--source-digest",
        "a" * 40,
        "--deny-self-hosted-runners",
        "--format",
        "json",
    ]


def test_the_snapshot_carries_the_release_bytes_for_this_runs_names(
    tmp_path: pth.Path,
) -> None:
    """Only assets sharing a name with this run's artefacts are downloaded."""
    fake = fake_release(tmp_path)
    link_scripts(tmp_path)
    publish = tmp_path / "dist" / "publish"
    publish.mkdir(parents=True)
    for name in ("git_donkey-1-py3-none-any.whl", _CARRIED_ASSET):
        (publish / name).write_bytes(b"rebuilt")
    for asset in (
        _CARRIED_ASSET,
        "git_donkey-0.9.tar.gz",
        "git_donkey-0.9-run1-1.sigstore.json",
    ):
        (fake.github / asset).write_bytes(b"published")
    fake.add_attestation(fake.github / _CARRIED_ASSET)

    completed = run_bash(
        step_script("draft-release", "Snapshot the release assets"),
        tmp_path,
        fake.env(),
    )

    assert completed.returncode == 0, completed.stderr
    verified = run_bash(
        step_script("draft-release", "Verify carried asset provenance"),
        tmp_path,
        fake.env(),
    )
    assert verified.returncode == 0, verified.stderr
    state = tmp_path / "github-state"
    carried = {path.name: path.read_bytes() for path in (state / "files").iterdir()}
    assert carried == {_CARRIED_ASSET: b"published"}, (
        "the PyPI job needs GitHub's bytes for exactly the names both share"
    )
    listed = json.loads((state / "assets.json").read_text(encoding="utf-8"))
    assert len(listed["assets"]) == _SNAPSHOT_ASSETS, (
        "the snapshot must list every asset"
    )
    assert [call[:2] for call in fake.calls("gh")] == [
        ["release", "view"],
        ["release", "download"],
        ["--version"],
        ["attestation", "verify"],
    ], "the step snapshots assets, then records the verification attempt"
    verify_call = fake.calls("gh")[-1]
    assert verify_call == _expected_verifier_call(), (
        "the verifier must bind the asset to the release identity policy"
    )
    record = json.loads(
        (state / "provenance" / f"{_CARRIED_ASSET}.json").read_text("utf-8")
    )
    subject = record[0]["verificationResult"]["statement"]["subject"][0]
    assert subject["name"] == _CARRIED_ASSET, (
        "the saved attestation must name the carried release asset"
    )


@pytest.mark.parametrize("is_prerelease", ["true", "false"])
def test_the_release_is_published_with_its_prerelease_status(
    tmp_path: pth.Path, is_prerelease: str
) -> None:
    """A draft is made visible, keeping the version check's status."""
    fake = fake_release(tmp_path)

    completed = run_bash(
        step_script("publish-release", "Publish the GitHub Release"),
        tmp_path,
        fake.env(IS_PRERELEASE=is_prerelease),
    )

    edits = [call for call in fake.calls("gh") if call[:2] == ["release", "edit"]]
    assert completed.returncode == 0, completed.stderr
    assert edits == [
        [
            "release",
            "edit",
            _TAG,
            "--draft=false",
            f"--prerelease={is_prerelease}",
        ]
    ], "the draft must be made visible once with the version check's status"
    assert fake.release_state() == {
        "isDraft": False,
        "isPrerelease": is_prerelease == "true",
    }, "the fake release must record the published state"


def test_a_published_release_is_not_edited_again(tmp_path: pth.Path) -> None:
    """A fully published re-run leaves the release untouched."""
    fake = fake_release(tmp_path)
    fake.create_release(draft=False, prerelease=False)

    completed = run_bash(
        step_script("publish-release", "Publish the GitHub Release"),
        tmp_path,
        fake.env(IS_PRERELEASE="false"),
    )

    assert completed.returncode == 0, completed.stderr
    assert not any(call[:2] == ["release", "edit"] for call in fake.calls("gh")), (
        "an unchanged release must not be edited"
    )


def test_a_mismatched_prerelease_flag_is_corrected(tmp_path: pth.Path) -> None:
    """A visible release with the wrong flag is edited to match the version."""
    fake = fake_release(tmp_path)
    fake.create_release(draft=False, prerelease=False)

    completed = run_bash(
        step_script("publish-release", "Publish the GitHub Release"),
        tmp_path,
        fake.env(IS_PRERELEASE="true"),
    )

    edits = [call for call in fake.calls("gh") if call[:2] == ["release", "edit"]]
    assert completed.returncode == 0, completed.stderr
    assert edits == [["release", "edit", _TAG, "--draft=false", "--prerelease=true"]], (
        "the pre-release flag must be corrected"
    )
    assert fake.release_state()["isPrerelease"] is True, (
        "the corrected flag must be recorded"
    )


_BUNDLE_ASSET = "git-donkey-v1.2.3-run1-1.attestations.jsonl"


def _bundle_bytes(names: tuple[str, ...]) -> bytes:
    """Render a combined bundle naming ``names`` at the published digest."""
    digest = hashlib.sha256(b"published").hexdigest()
    lines = []
    for name in names:
        statement = {"subject": [{"name": name, "digest": {"sha256": digest}}]}
        payload = base64.b64encode(json.dumps(statement).encode()).decode()
        lines.append(json.dumps({"dsseEnvelope": {"payload": payload}}))
    return ("\n".join(lines) + "\n").encode()


def _published_release(
    tmp_path: pth.Path, *, complete: bool, source_digest: str = "a" * 40
) -> tuple[FakeRelease, dict[str, str]]:
    """Seed a published release and PyPI, optionally missing one asset."""
    fake = fake_release(tmp_path)
    fake.create_release(draft=False, prerelease=False)
    link_scripts(tmp_path)
    publish = tmp_path / "dist" / "publish"
    publish.mkdir(parents=True)
    names = ("git_donkey-1-py3-none-any.whl", _CARRIED_ASSET)
    for name in names:
        (publish / name).write_bytes(b"published")
        (fake.pypi / name).write_bytes(b"published")
        (fake.github / name).write_bytes(b"published")
        fake.add_attestation(
            fake.github / name, AttestationOverrides(source_digest=source_digest)
        )
    (fake.github / _BUNDLE_ASSET).write_bytes(_bundle_bytes(names))
    if not complete:
        (fake.github / _CARRIED_ASSET).unlink()
    return fake, fake.env()


def _run_redraft_check(
    tmp_path: pth.Path, fake: FakeRelease, env: dict[str, str]
) -> dict[str, str]:
    """Run the index and completeness steps, returning their outputs."""
    fetch = run_bash(
        step_script("draft-release", "Fetch the PyPI index"), tmp_path, env
    )
    assert fetch.returncode == 0, fetch.stderr
    report = run_bash(
        step_script(
            "draft-release", "Check whether an already published release is complete"
        ),
        tmp_path,
        env,
    )
    assert report.returncode == 0, report.stderr
    return outputs(tmp_path / "github-output")


def _redraft_fires(written: dict[str, str], *, ok: str = "true") -> bool:
    """Return whether the workflow's re-draft guard would run the edit.

    The same expression is pinned in the workflow contract test; this helper
    lets the step tests exercise the branch the runner would take.

    Returns
    -------
    bool
        Whether the guard would run the re-draft edit.
    """
    return written.get("published") != "false" and (
        written.get("complete") != "true" or ok != "true"
    )


def _run_published_provenance(
    tmp_path: pth.Path, env: dict[str, str]
) -> dict[str, str]:
    """Run the published-release provenance step and return its outputs."""
    completed = run_bash(
        step_script("draft-release", "Verify published release provenance"),
        tmp_path,
        env,
    )
    assert completed.returncode == 0, completed.stderr
    return outputs(tmp_path / "github-output")


def test_an_incomplete_published_release_returns_to_draft(
    tmp_path: pth.Path,
) -> None:
    """A visible release missing an asset is hidden before reconciliation."""
    fake, env = _published_release(tmp_path, complete=False)

    written = _run_redraft_check(tmp_path, fake, env)
    completed = run_bash(
        step_script("draft-release", "Return an incomplete published release to draft"),
        tmp_path,
        env,
    )

    expected = {
        "attempts": "1",
        "status": "200",
        "published": "true",
        "complete": "false",
    }
    assert written == expected, "the check must report a published, incomplete release"
    assert completed.returncode == 0, completed.stderr
    edits = [call for call in fake.calls("gh") if call[:2] == ["release", "edit"]]
    assert edits == [["release", "edit", _TAG, "--draft=true"]], (
        "the incomplete release must be returned to draft once"
    )
    assert fake.release_state()["isDraft"] is True, (
        "the incomplete release must not stay visible"
    )


def test_a_complete_published_release_is_not_redrafted(tmp_path: pth.Path) -> None:
    """A fully reconciled published release is left visible and unchanged."""
    fake, env = _published_release(tmp_path, complete=True)

    written = _run_redraft_check(tmp_path, fake, env)
    provenance = _run_published_provenance(tmp_path, env)
    if _redraft_fires(written, ok=provenance["ok"]):
        completed = run_bash(
            step_script(
                "draft-release", "Return an incomplete published release to draft"
            ),
            tmp_path,
            env,
        )
        assert completed.returncode == 0, completed.stderr

    assert written["published"] == "true", "the release is published"
    assert written["complete"] == "true", "the release is complete"
    assert provenance["ok"] == "true", "the release carries current provenance"
    assert not any(call[:2] == ["release", "edit"] for call in fake.calls("gh")), (
        "a complete release must not be edited"
    )


def test_a_moved_tag_published_release_lacks_current_provenance(
    tmp_path: pth.Path,
) -> None:
    """A release attested for another commit is not complete for this tag."""
    fake, env = _published_release(tmp_path, complete=True, source_digest="b" * 40)

    written = _run_redraft_check(tmp_path, fake, env)
    provenance = _run_published_provenance(tmp_path, env)

    assert written["complete"] == "true", "digests and coverage still agree"
    assert provenance["ok"] == "false", (
        "provenance for another commit must not satisfy the release"
    )
    assert _redraft_fires(written, ok=provenance["ok"]), (
        "the release must be returned to draft for repair"
    )


def test_a_release_whose_bundle_omits_a_distribution_is_incomplete(
    tmp_path: pth.Path,
) -> None:
    """Matching digests are not complete when the bundle omits a file."""
    fake, env = _published_release(tmp_path, complete=True)
    (fake.github / _BUNDLE_ASSET).write_bytes(
        _bundle_bytes(("git_donkey-1-py3-none-any.whl",))
    )

    written = _run_redraft_check(tmp_path, fake, env)

    assert written["published"] == "true", "the release is published"
    assert written["complete"] == "false", (
        "a bundle that omits a distribution must not count as complete"
    )
    assert _redraft_fires(written), "the incomplete release must be re-drafted"


def test_a_draft_release_is_not_checked_for_completeness(
    tmp_path: pth.Path,
) -> None:
    """A draft is already hidden, so no completeness decision is needed."""
    fake = fake_release(tmp_path)
    link_scripts(tmp_path)
    (tmp_path / "dist" / "publish").mkdir(parents=True)

    written = _run_redraft_check(tmp_path, fake, fake.env())

    assert written["published"] == "false", "a draft is not published"
    assert "complete" not in written, "a draft needs no completeness verdict"
    assert not any(call[:2] == ["release", "edit"] for call in fake.calls("gh")), (
        "a draft must not be edited"
    )


def _carried_asset(fake: FakeRelease) -> tuple[pth.Path, dict[str, str]]:
    """Seed one local build and a differently built GitHub Release asset."""
    link_scripts(fake.root)
    publish = fake.root / "dist" / "publish"
    publish.mkdir(parents=True)
    (publish / _CARRIED_ASSET).write_bytes(b"fresh attested build")
    asset = fake.github / _CARRIED_ASSET
    asset.write_bytes(b"prior release asset")
    environment = fake.env()
    snapshot = run_bash(
        step_script("draft-release", "Snapshot the release assets"),
        fake.root,
        environment,
    )
    assert snapshot.returncode == 0, snapshot.stderr
    return asset, environment


@pytest.mark.parametrize(
    ("overrides", "extra_env"),
    [
        (None, {}),
        (AttestationOverrides(repository="attacker/other"), {}),
        (
            AttestationOverrides(
                signer_workflow="leynos/git-donkey/.github/workflows/other.yml"
            ),
            {},
        ),
        (AttestationOverrides(source_ref="refs/tags/v9.9.9"), {}),
        (AttestationOverrides(self_hosted=True), {}),
        (AttestationOverrides(sha256="0" * 64), {}),
        (
            AttestationOverrides(),
            {"FAKE_ATTEST_FAIL": "controlled verifier failure"},
        ),
    ],
    ids=[
        "missing-attestation",
        "wrong-repository",
        "wrong-workflow",
        "wrong-tag",
        "self-hosted-runner",
        "another-digest",
        "verifier-failure",
    ],
)
def test_failed_verification_warns_and_saves_no_record(
    tmp_path: pth.Path,
    overrides: AttestationOverrides | None,
    extra_env: dict[str, str],
) -> None:
    """The snapshot continues while unverified assets stay marked untrusted."""
    fake = fake_release(tmp_path)
    asset, environment = _carried_asset(fake)
    if overrides is not None:
        fake.add_attestation(asset, overrides)
    completed = run_bash(
        step_script("draft-release", "Verify carried asset provenance"),
        tmp_path,
        {**environment, **extra_env},
    )

    record = tmp_path / "github-state" / "provenance" / f"{_CARRIED_ASSET}.json"
    verify_calls = [
        call for call in fake.calls("gh") if call[:2] == ["attestation", "verify"]
    ]
    assert completed.returncode == 0, completed.stderr
    assert f"GitHub asset {_CARRIED_ASSET}" in completed.stdout, (
        "verification failure must warn about the specific carried asset"
    )
    assert not record.exists(), "failed verification must remove partial JSON output"
    assert len(verify_calls) == _VERIFY_ATTEMPTS, (
        "verification failures must stop after three attempts"
    )
    assert outputs(tmp_path / "github-output")["verification_failures"] == "1", (
        "a verifier warning must be reported to the handoff artefact"
    )


def test_transient_verification_failure_retries_before_saving_provenance(
    tmp_path: pth.Path,
) -> None:
    """A temporary verifier failure gets another bounded verification attempt."""
    fake = fake_release(tmp_path)
    asset, environment = _carried_asset(fake)
    fake.add_attestation(asset)

    completed = run_bash(
        step_script("draft-release", "Verify carried asset provenance"),
        tmp_path,
        {**environment, "FAKE_ATTEST_FAIL_COUNT": "1"},
    )

    verify_calls = [
        call for call in fake.calls("gh") if call[:2] == ["attestation", "verify"]
    ]
    record = tmp_path / "github-state" / "provenance" / f"{_CARRIED_ASSET}.json"
    assert completed.returncode == 0, completed.stderr
    assert len(verify_calls) == _TRANSIENT_ATTEMPTS, (
        "a first transient failure must be retried once"
    )
    assert verify_calls[0] == verify_calls[1], "each retry must preserve the policy"
    assert record.is_file(), "successful retry must save its approved verifier result"
    assert outputs(tmp_path / "github-output")["verification_failures"] == "0", (
        "a successful retry must not be reported as a remaining failure"
    )
    assert "Could not verify provenance" not in completed.stdout, (
        "a later successful attempt must not leave a failure warning"
    )


def test_the_verifier_requires_github_cli_2_102_0(tmp_path: pth.Path) -> None:
    """An older CLI cannot silently skip the repository-store policy flags."""
    fake = fake_release(tmp_path)
    asset, environment = _carried_asset(fake)
    fake.add_attestation(asset)
    completed = run_bash(
        step_script("draft-release", "Verify carried asset provenance"),
        tmp_path,
        {**environment, "FAKE_GH_VERSION": "2.101.9"},
    )

    assert completed.returncode != 0, "unsupported CLI versions must stop the step"
    assert "GitHub CLI 2.102.0 or newer is required" in completed.stderr, (
        "the step must explain which CLI version it requires"
    )
    record = tmp_path / "github-state" / "provenance" / f"{_CARRIED_ASSET}.json"
    assert not record.exists(), "an older CLI must not save provenance output"
    assert not any(
        call[:2] == ["attestation", "verify"] for call in fake.calls("gh")
    ), "an older CLI must fail before invoking the verifier"


def test_an_empty_carryover_does_not_require_the_attestation_cli_version(
    tmp_path: pth.Path,
) -> None:
    """No verifier invocation means an older CLI version is irrelevant."""
    fake = fake_release(tmp_path)
    state = tmp_path / "github-state"
    state.mkdir()
    (state / "carryover.txt").write_text("", encoding="utf-8")

    completed = run_bash(
        step_script("draft-release", "Verify carried asset provenance"),
        tmp_path,
        fake.env(FAKE_GH_VERSION="2.101.9"),
    )

    assert completed.returncode == 0, completed.stderr
    assert outputs(tmp_path / "github-output")["verification_failures"] == "0", (
        "the output must be present when no asset needs verification"
    )
    assert not fake.calls("gh"), "an empty carryover must not invoke the verifier"
    assert (state / "provenance").is_dir(), (
        "the handoff must still contain an empty provenance directory"
    )


def test_a_missing_carryover_list_fails_closed(tmp_path: pth.Path) -> None:
    """Missing snapshot state cannot be mistaken for an empty asset list."""
    fake = fake_release(tmp_path)

    completed = run_bash(
        step_script("draft-release", "Verify carried asset provenance"),
        tmp_path,
        fake.env(),
    )

    assert completed.returncode != 0, "a missing carryover list must fail the step"
    assert "Carryover list is missing" in completed.stderr, (
        "the failure must identify the missing snapshot state"
    )
    assert not fake.calls("gh"), "the step must fail before querying GitHub"


def test_the_fake_gh_rejects_operations_the_workflow_does_not_use(
    tmp_path: pth.Path,
) -> None:
    """An unsupported fake operation cannot look like a successful API call."""
    fake = fake_release(tmp_path)

    completed = run_bash("gh api user", tmp_path, fake.env())

    assert completed.returncode == _UNSUPPORTED_STATUS, (
        "unsupported fake gh calls must fail explicitly"
    )
    assert "unsupported fake gh operation: api user" in completed.stderr, (
        "the fake gh error must identify the unsupported operation"
    )
