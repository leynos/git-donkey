"""Behaviour of the draft-release completeness and re-draft steps.

These tests exercise the steps that decide whether an already published
release is complete, verify its provenance, and return a positively
incomplete release to draft without changing it when a check is
inconclusive.
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
    import subprocess

_TAG = "v1.2.3"
_CARRIED_ASSET = "git_donkey-1.tar.gz"

pytestmark = [
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="The release workflow's steps run under Bash on Linux.",
    ),
    pytest.mark.timeout(120),
]


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


def _run_redraft_steps(
    tmp_path: pth.Path, env: dict[str, str]
) -> tuple[
    subprocess.CompletedProcess[str],
    subprocess.CompletedProcess[str] | None,
    dict[str, str],
]:
    """Run the index and completeness steps, returning both results."""
    fetch = run_bash(
        step_script("draft-release", "Fetch the PyPI index"), tmp_path, env
    )
    report = (
        run_bash(
            step_script(
                "draft-release",
                "Check whether an already published release is complete",
            ),
            tmp_path,
            env,
        )
        if fetch.returncode == 0
        else None
    )
    return fetch, report, outputs(tmp_path / "github-output")


def _run_redraft_check(tmp_path: pth.Path, env: dict[str, str]) -> dict[str, str]:
    """Run the redraft steps and require both to succeed."""
    fetch, report, written = _run_redraft_steps(tmp_path, env)
    assert fetch.returncode == 0, fetch.stderr
    assert report is not None, "the completeness step must run"
    assert report.returncode == 0, report.stderr
    return written


def _redraft_fires(written: dict[str, str]) -> bool:
    """Return whether the workflow's re-draft guard would run the edit.

    The same expression is pinned in the workflow contract test; this helper
    lets the step tests exercise the branch the runner would take.

    Returns
    -------
    bool
        Whether the guard would run the re-draft edit.
    """
    return written.get("published") == "true" and written.get("complete") == "false"


def _run_published_provenance(
    tmp_path: pth.Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run the published-release provenance step."""
    return run_bash(
        step_script("draft-release", "Verify published release provenance"),
        tmp_path,
        env,
    )


def test_an_incomplete_published_release_returns_to_draft(
    tmp_path: pth.Path,
) -> None:
    """A visible release missing an asset is hidden before reconciliation."""
    fake, env = _published_release(tmp_path, complete=False)

    written = _run_redraft_check(tmp_path, env)
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

    written = _run_redraft_check(tmp_path, env)
    provenance = _run_published_provenance(tmp_path, env)

    assert provenance.returncode == 0, provenance.stderr
    assert written["published"] == "true", "the release is published"
    assert written["complete"] == "true", "the release is complete"
    assert not _redraft_fires(written), "a complete release must not be redrafted"
    assert not any(call[:2] == ["release", "edit"] for call in fake.calls("gh")), (
        "a complete release must not be edited"
    )


def test_a_moved_tag_published_release_lacks_current_provenance(
    tmp_path: pth.Path,
) -> None:
    """A release attested for another commit is not complete for this tag."""
    fake, env = _published_release(tmp_path, complete=True, source_digest="b" * 40)

    fetch, report, written = _run_redraft_steps(tmp_path, env)

    assert fetch.returncode == 0, fetch.stderr
    assert report is not None, "the completeness step must run"
    assert report.returncode != 0, (
        "an inconclusive bundle must stop the completeness check"
    )
    assert "No covering bundle verified" in report.stderr, (
        "the failure must identify the unverified bundle"
    )
    assert written["published"] == "true", "the release is published"
    assert not any(call[:2] == ["release", "edit"] for call in fake.calls("gh")), (
        "an inconclusive verdict must not edit the release"
    )
    assert (fake.github / _BUNDLE_ASSET).exists(), (
        "the bundle must be preserved for investigation"
    )
    provenance = _run_published_provenance(tmp_path, env)
    assert provenance.returncode != 0, (
        "provenance for another commit must not satisfy the release"
    )


def test_a_release_whose_bundle_omits_a_distribution_is_incomplete(
    tmp_path: pth.Path,
) -> None:
    """Matching digests are not complete when the bundle omits a file."""
    fake, env = _published_release(tmp_path, complete=True)
    (fake.github / _BUNDLE_ASSET).write_bytes(
        _bundle_bytes(("git_donkey-1-py3-none-any.whl",))
    )

    written = _run_redraft_check(tmp_path, env)

    assert written["published"] == "true", "the release is published"
    assert written["complete"] == "false", (
        "a bundle that omits a distribution must not count as complete"
    )
    assert _redraft_fires(written), "the incomplete release must be re-drafted"


def test_a_transient_bundle_verification_failure_is_retried(
    tmp_path: pth.Path,
) -> None:
    """A temporary verifier failure is retried, not treated as invalid."""
    fake, env = _published_release(tmp_path, complete=True)

    written = _run_redraft_check(tmp_path, {**env, "FAKE_ATTEST_FAIL_COUNT": "1"})

    assert written["complete"] == "true", (
        "a retried bundle verification must still count"
    )
    assert (fake.github / _BUNDLE_ASSET).exists(), "a valid bundle must not be deleted"


def test_a_persistently_unverifiable_bundle_is_preserved(
    tmp_path: pth.Path,
) -> None:
    """An inconclusive verifier failure never deletes a release asset."""
    fake, env = _published_release(tmp_path, complete=True)

    fetch, report, written = _run_redraft_steps(
        tmp_path, {**env, "FAKE_ATTEST_FAIL": "persistent failure"}
    )

    assert fetch.returncode == 0, fetch.stderr
    assert report is not None, "the completeness step must run"
    assert report.returncode != 0, (
        "an inconclusive bundle must stop the completeness check"
    )
    assert written["published"] == "true", "the release is published"
    assert not _redraft_fires(written), "an unknown verdict must not redraft"
    assert (fake.github / _BUNDLE_ASSET).exists(), (
        "the workflow must preserve an asset whose verdict is unknown"
    )
    assert not any(
        call[:2] == ["release", "delete-asset"] for call in fake.calls("gh")
    ), "the workflow must not delete release assets"


@pytest.mark.parametrize(
    ("failure", "value"),
    [
        ("FAKE_BUNDLE_FAIL", "controlled verifier failure"),
        ("FAKE_ATTEST_FAIL", "controlled verifier failure"),
        ("FAKE_DOWNLOAD_FAIL", "*.attestations.jsonl"),
    ],
    ids=["bundle-verifier", "store-verifier", "bundle-download"],
)
def test_a_digest_mismatch_redrafts_despite_failed_checks(
    tmp_path: pth.Path, failure: str, value: str
) -> None:
    """Destination metadata proves the release incomplete before other checks."""
    fake, env = _published_release(tmp_path, complete=True)
    (fake.github / _CARRIED_ASSET).write_bytes(b"clobbered by an earlier workflow")

    fetch, report, written = _run_redraft_steps(tmp_path, {**env, failure: value})

    assert fetch.returncode == 0, fetch.stderr
    assert report is not None, "the completeness step must run"
    assert report.returncode == 0, (
        "a metadata-confirmed mismatch must not stop at another failed check"
    )
    assert written["complete"] == "false", "the digest mismatch is confirmed"
    assert _redraft_fires(written), "the incomplete release must be redrafted"
    completed = run_bash(
        step_script("draft-release", "Return an incomplete published release to draft"),
        tmp_path,
        env,
    )
    assert completed.returncode == 0, completed.stderr
    assert fake.release_state()["isDraft"] is True, (
        "the confirmed incomplete release must be hidden"
    )


def test_a_starter_asset_with_a_digest_mismatch_still_redrafts(
    tmp_path: pth.Path,
) -> None:
    """An incomplete upload is a confirmed gap, not an unavailable check."""
    fake, env = _published_release(tmp_path, complete=True)
    fake.asset_states[_CARRIED_ASSET] = "starter"
    (fake.github / "git_donkey-1-py3-none-any.whl").write_bytes(b"clobbered")

    fetch, report, written = _run_redraft_steps(tmp_path, env)

    assert fetch.returncode == 0, fetch.stderr
    assert report is not None, "the completeness step must run"
    assert report.returncode == 0, (
        "the metadata check must treat the starter asset as incomplete"
    )
    assert written["complete"] == "false", "the release is confirmed incomplete"
    assert _redraft_fires(written), "the incomplete release must be redrafted"


def test_an_additional_starter_asset_still_redrafts(tmp_path: pth.Path) -> None:
    """Any incomplete upload marks the release incomplete, even an extra one."""
    fake, env = _published_release(tmp_path, complete=True)
    (fake.github / "extra-asset.whl").write_bytes(b"extra")
    fake.asset_states["extra-asset.whl"] = "starter"

    fetch, report, written = _run_redraft_steps(tmp_path, env)

    assert fetch.returncode == 0, fetch.stderr
    assert report is not None, "the completeness step must run"
    assert report.returncode == 0, (
        "an additional starter asset must still reach the redraft decision"
    )
    assert written["complete"] == "false", "the release is confirmed incomplete"
    assert _redraft_fires(written), "the incomplete release must be redrafted"


def test_a_bundle_that_fails_verification_does_not_redraft(
    tmp_path: pth.Path,
) -> None:
    """A covering bundle that fails verification stops without editing."""
    fake, env = _published_release(tmp_path, complete=True)

    fetch, report, written = _run_redraft_steps(
        tmp_path, {**env, "FAKE_BUNDLE_FAIL": "bundle signature invalid"}
    )

    assert fetch.returncode == 0, fetch.stderr
    assert report is not None, "the completeness step must run"
    assert report.returncode != 0, "an unverifiable covering bundle must fail"
    assert written["published"] == "true", "the release is published"
    assert not _redraft_fires(written), "an unverifiable bundle must not redraft"
    assert (fake.github / _BUNDLE_ASSET).exists(), (
        "the bundle must be preserved for investigation"
    )
    assert not any(call[:2] == ["release", "edit"] for call in fake.calls("gh")), (
        "an unverifiable bundle must not edit the release"
    )


def test_a_failed_index_lookup_does_not_redraft(tmp_path: pth.Path) -> None:
    """An unavailable index is inconclusive, not an incomplete release."""
    fake, env = _published_release(tmp_path, complete=True)

    fetch, report, written = _run_redraft_steps(
        tmp_path, {**env, "FAKE_CURL_STATUSES": "500"}
    )

    assert fetch.returncode != 0, "the index lookup must fail"
    assert report is None, "the completeness check must not run"
    assert "complete" not in written, "no completeness verdict may be recorded"
    assert "published" not in written, "no publication verdict may be recorded"
    assert not any(call[:2] == ["release", "edit"] for call in fake.calls("gh")), (
        "an unavailable index must not edit the release"
    )


def test_a_failed_bundle_download_does_not_redraft(tmp_path: pth.Path) -> None:
    """An unavailable bundle download is inconclusive."""
    fake, env = _published_release(tmp_path, complete=True)

    fetch, report, written = _run_redraft_steps(
        tmp_path, {**env, "FAKE_DOWNLOAD_FAIL": "*.attestations.jsonl"}
    )

    assert fetch.returncode == 0, fetch.stderr
    assert report is not None, "the completeness step must run"
    assert report.returncode != 0, (
        "a failed bundle download must stop the completeness check"
    )
    assert written["published"] == "true", "the release is known to be published"
    assert not _redraft_fires(written), "an unknown verdict must not redraft"
    assert (fake.github / _BUNDLE_ASSET).exists(), (
        "the bundle must be preserved after a download failure"
    )


def test_an_empty_published_release_is_incomplete(tmp_path: pth.Path) -> None:
    """A published release with no assets or bundle is incomplete."""
    fake = fake_release(tmp_path)
    fake.create_release(draft=False, prerelease=False)
    link_scripts(tmp_path)
    (tmp_path / "dist" / "publish").mkdir(parents=True)

    written = _run_redraft_check(tmp_path, fake.env())

    assert written["published"] == "true", "the release is published"
    assert written["complete"] == "false", (
        "an empty release has no combined bundle and must be incomplete"
    )
    assert _redraft_fires(written), "the empty release must be re-drafted"


def test_a_draft_release_is_not_checked_for_completeness(
    tmp_path: pth.Path,
) -> None:
    """A draft is already hidden, so no completeness decision is needed."""
    fake = fake_release(tmp_path)
    link_scripts(tmp_path)
    (tmp_path / "dist" / "publish").mkdir(parents=True)

    written = _run_redraft_check(tmp_path, fake.env())

    assert written["published"] == "false", "a draft is not published"
    assert "complete" not in written, "a draft needs no completeness verdict"
    assert not any(call[:2] == ["release", "edit"] for call in fake.calls("gh")), (
        "a draft must not be edited"
    )
