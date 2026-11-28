"""Structural contracts of the publish-release and draft-release steps.

These read the parsed workflow rather than executing it: what gates the PyPI
upload, how the release bundle is verified and assembled, when a complete
rerun skips assembly, and when a published release is returned to draft.
"""

from __future__ import annotations

import typing as typ

from tests.release_workflow import jobs, step, step_script

_PROVENANCE_STEP = "Verify provenance of PyPI bytes"
_PROVENANCE_DIR = "pypi-provenance"
_ASSEMBLY_STEP = "Assemble the release attestations"
_GH_VERSION_COMMAND = "gh --version | python3 scripts/release_gh_version.py"


def _job(name: str) -> dict[str, typ.Any]:
    """Return one release job."""
    return typ.cast("dict[str, typ.Any]", jobs("release.yml")[name])


def _needs(name: str) -> set[str]:
    """Return every job ``name`` waits on, directly or transitively."""
    declared = _job(name).get("needs", [])
    direct = {declared} if isinstance(declared, str) else set(declared)
    return direct.union(*(_needs(parent) for parent in direct))


def test_the_release_is_published_only_after_pypi() -> None:
    """A failed upload or a digest mismatch leaves the GitHub Release a draft."""
    assert {"draft-release", "publish-pypi"} <= _needs("publish-release"), (
        "publish-release must wait for the draft and for PyPI"
    )
    assert "publish-pypi" in _job("publish-release")["needs"], (
        "publish-release must wait directly on successful PyPI publication"
    )
    assert "--draft" in step_script(
        "draft-release", "Create or reuse the draft release"
    ), "the first release must be created as a draft"
    names = [item.get("name") for item in _job("publish-release")["steps"]]
    fetched = names.index("Check the fetched PyPI bytes")
    provenance = names.index(_PROVENANCE_STEP)
    upload = names.index("Upload the assets the release lacks")
    attestations = names.index(_ASSEMBLY_STEP)
    check = names.index("Check both destinations hold the same bytes")
    assert (
        fetched
        < provenance
        < upload
        < attestations
        < check
        < names.index("Publish the GitHub Release")
    ), (
        "provenance must precede the upload, the bundle must be assembled "
        "from the uploaded bytes, and the digests must be compared before "
        "the release"
    )
    publish = step_script("publish-release", "Publish the GitHub Release")
    assert "--draft=false" in publish, "the release must stop being a draft"
    assert 'if [ "${is_draft}" = true ]' in publish, (
        "the release must be edited only when its state differs"
    )


def test_pypi_bytes_are_verified_before_the_release_upload() -> None:
    """Fetched PyPI bytes need approved provenance, not just the index digest."""
    found = step("publish-release", _PROVENANCE_STEP)
    assert "if" not in found, (
        "the gate must run unconditionally, even with an empty fetch list"
    )
    script = step_script("publish-release", _PROVENANCE_STEP)
    for fragment in (
        _GH_VERSION_COMMAND,
        f"mkdir -p {_PROVENANCE_DIR}",
        '--repo "${GITHUB_REPOSITORY}"',
        '--signer-workflow "${GITHUB_REPOSITORY}/.github/workflows/release.yml"',
        '--source-ref "${GITHUB_REF}"',
        '--source-digest "${GITHUB_SHA}"',
        "--predicate-type https://slsa.dev/provenance/v1",
        "--deny-self-hosted-runners",
        "--format json",
        f'record="{_PROVENANCE_DIR}/${{name}}.json"',
        'rm -f "${record}"',
        "failed=0",
        "failed=1",
        '[ "${failed}" -eq 0 ] || exit 1',
        "::error title=pypi-provenance::",
        "check-provenance --publish dist/publish",
        "--index pypi-index.json",
        "--output github-upload",
        f"--provenance {_PROVENANCE_DIR}",
    ):
        assert fragment in script, f"the provenance gate must include {fragment!r}"
    assert "${{" not in script, (
        "the provenance script must not interpolate workflow expressions"
    )
    assert "done < pypi-fetch.tsv" in script, (
        "only the files actually fetched from PyPI need verification"
    )
    loop = script.partition("done < pypi-fetch.tsv")[0]
    assert "exit 1" not in loop, (
        "a failed verification must not stop the remaining checks"
    )
    assert script.index("done < pypi-fetch.tsv") < script.index(
        '[ "${failed}" -eq 0 ] || exit 1'
    ), "the gate must finish verifying every file before it exits"


def test_the_attached_bundle_is_verified_before_coverage() -> None:
    """An attached bundle counts only after it verifies for every file."""
    coverage = step("publish-release", "Check the attached bundle coverage")
    assert coverage["id"] == "coverage", "the coverage result feeds the assembly"
    script = step_script("publish-release", "Check the attached bundle coverage")
    for fragment in (
        "gh release download",
        "--bundle",
        '--source-digest "${GITHUB_SHA}"',
        "bundle-coverage",
        'covered="$(grep',
        'if [ "${covered}" = true ]',
        "covering+=(",
        'for bundle in "${covering[@]}"',
        "verified_bundle",
        "No covering bundle verified",
        "for attempt in 1 2 3",
        "--bundles release-bundles",
    ):
        assert fragment in script, f"the coverage check must include {fragment!r}"
    names = [item.get("name") for item in _job("publish-release")["steps"]]
    assert (
        names.index("List the release assets")
        < names.index("Check the attached bundle coverage")
        < names.index("Stage the assets the release lacks")
    ), "coverage must be known before staging decides whether to rebuild"


def test_the_assembly_verifies_the_bundle_it_uploads() -> None:
    """The assembled bundle must name every distribution before upload."""
    assembly = step("publish-release", _ASSEMBLY_STEP)
    assert assembly["if"] == (
        "steps.stage.outputs.pending == 'true' "
        "|| steps.coverage.outputs.covered == 'false'"
    ), "the assembly must run only when the release needs a new bundle"
    script = step_script("publish-release", _ASSEMBLY_STEP)
    for fragment in (
        "gh attestation verify",
        '--source-ref "${GITHUB_REF}"',
        '--source-digest "${GITHUB_SHA}"',
        "gh attestation download",
        "--predicate-type https://slsa.dev/provenance/v1",
        'gh release download "${GITHUB_REF_NAME}"',
        ".attestations.jsonl",
        "--bundle assembled-attestations/bundles.jsonl",
        "::error title=release-attestations::",
        'gh release upload "${GITHUB_REF_NAME}" "${bundle}"',
    ):
        assert fragment in script, f"the bundle assembly must include {fragment!r}"
    assert "${{" not in script, (
        "the bundle assembly must not interpolate workflow expressions"
    )
    upload = step("publish-release", "Upload the assets the release lacks")
    assert upload["if"] == "steps.stage.outputs.files == 'true'", (
        "the distribution upload must run only when files were staged"
    )
    names = [item.get("name") for item in _job("publish-release")["steps"]]
    assert (
        names.index("Upload the assets the release lacks")
        < names.index(_ASSEMBLY_STEP)
        < names.index("Check both destinations hold the same bytes")
    ), "the bundle must cover the bytes already uploaded to the release"


def test_the_final_check_reads_a_refreshed_index() -> None:
    """The destination check must not trust an index fetched before uploads."""
    names = [item.get("name") for item in _job("publish-release")["steps"]]
    refresh = names.index("Refresh the PyPI index before the final check")
    verify = names.index("Check both destinations hold the same bytes")
    provenance = names.index(_PROVENANCE_STEP)
    assert provenance < refresh < verify, (
        "the index must be refreshed after the uploads and before the check"
    )
    refreshed = step_script(
        "publish-release", "Refresh the PyPI index before the final check"
    )
    assert refreshed == step_script("publish-release", "Fetch the PyPI index"), (
        "both lookups must run the same bounded fetch"
    )
    assert "pypi-index.json" in step_script(
        "publish-release", "Check both destinations hold the same bytes"
    ), "the check must read the file the refresh writes"


def test_the_final_check_pins_the_bundle_it_verified() -> None:
    """Only the bundle this run approved may stand in at publication."""
    found = step("publish-release", "Check both destinations hold the same bytes")
    env = typ.cast("dict[str, str]", found["env"])
    assert "steps.attestations.outputs.bundle" in env["EXPECTED_BUNDLE"], (
        "the assembled bundle must take precedence"
    )
    assert "steps.coverage.outputs.verified_bundle" in env["EXPECTED_BUNDLE"], (
        "a complete re-run must pin the covering bundle it verified"
    )
    assert "steps.attestations.outputs.bundle_digest" in env["EXPECTED_DIGEST"], (
        "the pinned bundle must also be pinned to its digest"
    )
    script = step_script(
        "publish-release", "Check both destinations hold the same bytes"
    )
    assert "--expected-bundle" in script, (
        "the check must pass the pinned bundle to the verifier"
    )
    assert "--expected-digest" in script, (
        "the check must pass the pinned digest to the verifier"
    )
    assembly = step_script("publish-release", _ASSEMBLY_STEP)
    assert "printf 'bundle=%s" in assembly, (
        "the assembly must report the bundle it uploaded"
    )
    assert "printf 'bundle_digest=%s" in assembly, (
        "the assembly must report the uploaded bundle's digest"
    )
    coverage = step_script("publish-release", "Check the attached bundle coverage")
    assert "printf 'verified_bundle=%s" in coverage, (
        "the coverage step must report the bundle it verified"
    )
    assert "printf 'verified_digest=%s" in coverage, (
        "the coverage step must report the verified bundle's digest"
    )


def test_a_complete_rerun_verifies_the_release_read_only() -> None:
    """A complete rerun skips the assembly and downloads no attestations."""
    verify = step("publish-release", "Verify release distributions")
    assert verify["if"] == (
        "steps.stage.outputs.pending != 'true' "
        "&& steps.coverage.outputs.covered == 'true'"
    ), "a complete re-run must verify the existing release read-only"
    script = step_script("publish-release", "Verify release distributions")
    for fragment in (
        "gh attestation verify",
        '--source-digest "${GITHUB_SHA}"',
        "::error title=release-provenance::",
    ):
        assert fragment in script, (
            f"the read-only verification must include {fragment!r}"
        )
    assert "attestation download" not in script, (
        "a complete re-run must not download attestations"
    )
    names = [item.get("name") for item in _job("publish-release")["steps"]]
    assert (
        names.index(_ASSEMBLY_STEP)
        < names.index("Verify release distributions")
        < names.index("Check both destinations hold the same bytes")
    ), "read-only verification must follow the assembly decision"


def test_the_redraft_check_compares_metadata_before_bundles() -> None:
    """Destination digests decide before any bundle download or verification."""
    names = [item.get("name") for item in _job("draft-release")["steps"]]
    check = names.index("Check whether an already published release is complete")
    provenance = names.index("Verify published release provenance")
    edit = names.index("Return an incomplete published release to draft")
    assert check < provenance < edit, "the provenance check must precede the edit"
    script = step_script(
        "draft-release", "Check whether an already published release is complete"
    )
    for fragment in (
        "--json isDraft,assets",
        "destinations-complete",
        'digests_complete="$(grep',
        "gh release download",
        "--bundle",
        "bundle-coverage",
        'complete="$(grep',
        'if [ "${complete}" = true ]',
        "covering+=(",
        'for bundle in "${covering[@]}"',
        "verified_bundle",
        "No covering bundle verified",
        "for attempt in 1 2 3",
        "::error title=bundle-verification::",
        "release-complete",
        "--index pypi-index.json",
        "--bundles release-bundles",
    ):
        assert fragment in script, f"the re-draft check must include {fragment!r}"
    assert script.index("destinations-complete") < script.index(
        "gh release download"
    ), "destination digests must be checked before any bundle download"
    assert script.index("release-complete") < script.index("gh attestation verify"), (
        "destination metadata must be checked before bundle verification"
    )


def test_an_incomplete_published_release_returns_to_draft() -> None:
    """A visible release that is not complete is hidden before repair."""
    provenance_script = step_script(
        "draft-release", "Verify published release provenance"
    )
    for fragment in (
        "gh attestation verify",
        '--source-ref "${GITHUB_REF}"',
        '--source-digest "${GITHUB_SHA}"',
        '--signer-workflow "${GH_REPO}/.github/workflows/release.yml"',
        "::error title=release-provenance::",
        "exit 1",
    ):
        assert fragment in provenance_script, (
            f"the published provenance check must include {fragment!r}"
        )
    assert (
        step("draft-release", "Create or reuse the draft release")["id"] == "create"
    ), "the guard reads the create step outcome"
    assert (
        step("draft-release", "Check whether an already published release is complete")[
            "id"
        ]
        == "redraft"
    ), "the guard reads the completeness output"
    assert step("draft-release", "Verify published release provenance")["id"] == (
        "provenance"
    ), "the guard reads the provenance output"
    edit_step = step("draft-release", "Return an incomplete published release to draft")
    assert edit_step["if"] == (
        "always() && steps.create.outcome == 'success' "
        "&& steps.redraft.outputs.published == 'true' "
        "&& steps.redraft.outputs.complete == 'false'"
    ), "the edit must run only for a confirmed incomplete release"
    assert "--draft=true" in step_script(
        "draft-release", "Return an incomplete published release to draft"
    ), "the release must be returned to draft"
