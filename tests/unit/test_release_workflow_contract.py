"""Structural contracts of the release workflow and of every ``run`` script.

These read the parsed workflow rather than executing it: which job holds which
token scope, what gates the upload, and which values may reach a shell. The
executed behaviour of the same steps lives in ``test_release_publish_steps.py``,
``test_release_github_steps.py``, and ``test_release_reconciliation.py``.
"""

from __future__ import annotations

import typing as typ

from tests.release_contract import (
    PINNED,
    ci_documents,
    declared_steps,
    document,
    document_paths,
    run_scripts,
)
from tests.release_workflow import (
    WORKFLOW,
    jobs,
    step,
    step_script,
    workflow_document,
)

_PYPROJECT_CHECK = "Check the pyproject.toml version against the tag"
_GH_VERSION_CHECK = "Check the GitHub CLI version"
_GH_VERSION_COMMAND = "gh --version | python3 scripts/release_gh_version.py"
_FETCH_INDEX = "Fetch the PyPI index"
_PROVENANCE_STEP = "Verify provenance of PyPI bytes"
_PROVENANCE_DIR = "pypi-provenance"
_ASSEMBLY_STEP = "Assemble the release attestations"

#: Each job's exact token scopes. Only the GitHub Release jobs may write
#: repository contents, and only the attesting and uploading jobs may mint an
#: OpenID Connect token. `publish-pypi` reads contents only to check out
#: `scripts/`; it sees the draft through `draft-release`'s snapshot instead.
_EXPECTED_PERMISSIONS: typ.Final = {
    "check-version": {"contents": "read"},
    "build": {"contents": "read"},
    "attest": {"contents": "read", "id-token": "write", "attestations": "write"},
    "draft-release": {"contents": "write", "attestations": "read"},
    "publish-pypi": {"contents": "read", "id-token": "write"},
    "publish-release": {"contents": "write", "attestations": "read"},
}


def _job(name: str) -> dict[str, typ.Any]:
    """Return one release job."""
    return typ.cast("dict[str, typ.Any]", jobs(WORKFLOW)[name])


def _needs(name: str) -> set[str]:
    """Return every job ``name`` waits on, directly or transitively."""
    declared = _job(name).get("needs", [])
    direct = {declared} if isinstance(declared, str) else set(declared)
    return direct.union(*(_needs(parent) for parent in direct))


def _uses(job_name: str, prefix: str) -> dict[str, typ.Any]:
    """Return the single step of ``job_name`` that uses action ``prefix``."""
    found = [
        item
        for item in _job(job_name)["steps"]
        if str(item.get("uses", "")).startswith(f"{prefix}@")
    ]
    assert len(found) == 1, f"{job_name} must use {prefix} exactly once"
    assert PINNED.search(found[0]["uses"]), f"{prefix} must be pinned by commit SHA"
    return typ.cast("dict[str, typ.Any]", found[0])


def test_every_job_holds_only_the_scopes_it_needs() -> None:
    """Nothing inherits a write scope from the top level."""
    assert workflow_document(WORKFLOW).get("permissions") == {}, (
        "release.yml must grant nothing at the top level"
    )
    declared = {name: _job(name).get("permissions") for name in jobs(WORKFLOW)}
    assert declared == _EXPECTED_PERMISSIONS, (
        "every release job must declare exactly its own token scopes"
    )


def test_every_checkout_disables_persisted_credentials() -> None:
    """No checkout leaves a usable token on disk, in any workflow or action."""
    documents = ci_documents()
    checkouts = [
        item
        for parsed in documents.values()
        for item in declared_steps(typ.cast("dict[str, typ.Any]", parsed))
        if str(item.get("uses", "")).startswith("actions/checkout@")
    ]
    assert checkouts, "the scan must reach the repository's checkouts"
    assert all(
        typ.cast("dict[str, typ.Any]", item.get("with", {})).get("persist-credentials")
        is False
        for item in checkouts
    ), "every checkout must disable persisted credentials"


def test_every_remote_action_is_pinned_to_a_commit() -> None:
    """A floating tag could change what a release run executes."""
    steps = [
        item for path in document_paths() for item in declared_steps(document(path))
    ]
    remote = [
        str(item["uses"])
        for item in steps
        if "uses" in item and not str(item["uses"]).startswith("./")
    ]
    assert remote, "the scan must reach the repository's remote actions"
    assert all(PINNED.search(action) for action in remote), (
        "every remote action must be pinned by commit SHA"
    )


def test_every_publishing_job_checks_the_live_tag_commit() -> None:
    """A run must stop if the tag has moved to another commit."""
    for job_name in ("draft-release", "publish-pypi", "publish-release"):
        names = [item.get("name") for item in _job(job_name)["steps"]]
        assert "Check the tag still points to this run's commit" in names, (
            f"{job_name} must resolve the live tag commit"
        )
        script = step_script(
            job_name, "Check the tag still points to this run's commit"
        )
        for fragment in (
            "git ls-remote origin",
            "refs/tags/${GITHUB_REF_NAME}^{}",
            '!= "${GITHUB_SHA}"',
            "::error title=tag-moved::",
        ):
            assert fragment in script, f"{job_name} must include {fragment!r}"


def test_the_pypi_upload_runs_in_the_pypi_environment() -> None:
    """Trusted Publishing is bound to the protected ``pypi`` environment."""
    assert _job("publish-pypi")["environment"] == {
        "name": "pypi",
        "url": "https://pypi.org/project/git-donkey/",
    }, "the PyPI upload must run in the protected pypi environment"


def test_a_repushed_tag_queues_instead_of_cancelling() -> None:
    """A cancelled release could stop part-way through the upload."""
    concurrency = workflow_document(WORKFLOW)["concurrency"]
    assert concurrency == {
        "group": "release-${{ github.ref }}",
        "cancel-in-progress": False,
    }, "a re-pushed tag must queue behind the running release"


def test_the_index_lookup_retries_and_is_bounded() -> None:
    """Both index readers share one bounded, retrying, timed-out lookup."""
    script = step_script("publish-pypi", _FETCH_INDEX)
    assert step_script("publish-release", _FETCH_INDEX) == script, (
        "publish-release must read the index through the same script"
    )
    assert step_script("draft-release", _FETCH_INDEX) == script, (
        "draft-release must read the index through the same script"
    )
    for fragment in (
        'while [ "${attempt}" -lt 6 ]; do',
        "--connect-timeout 10",
        "--max-time 60",
        "000|429|5??)",
        "attempts=%s",
        "https://pypi.org/simple/git-donkey/",
    ):
        assert fragment in script, f"the index lookup must include {fragment!r}"
    assert "404) echo '{\"files\": []}' > pypi-index.json ;;" in script, (
        "a project absent from PyPI must still read as an empty index"
    )


def test_every_publishing_job_waits_on_the_version_check() -> None:
    """No attestation, upload, or release happens for a mismatched tag."""
    assert _needs("publish-pypi") >= {"attest", "draft-release"}, (
        "PyPI needs the draft's snapshot to carry GitHub's bytes over"
    )
    assert "publish-pypi" in _needs("publish-release"), (
        "GitHub's missing assets take PyPI's bytes, so PyPI goes first"
    )
    for name in ("attest", "publish-pypi", "draft-release", "publish-release"):
        assert "check-version" in _needs(name), f"{name} must wait on check-version"
    assert "check-version" not in _needs("build"), (
        "the build must run beside the version check, not behind it"
    )
    pyproject = step("check-version", _PYPROJECT_CHECK)
    assert pyproject["env"] == {"TAG_VERSION": "${{ github.ref_name }}"}, (
        "the pyproject check must compare against the pushed tag"
    )


def test_the_cli_version_is_checked_before_anything_is_published() -> None:
    """An old CLI must stop the release before PyPI holds any of its files."""
    found = step("check-version", _GH_VERSION_CHECK)
    assert "if" not in found, (
        "the early gate must run on every release, including one with no carryover"
    )
    assert step_script("check-version", _GH_VERSION_CHECK).strip() == (
        f"set -euo pipefail\n{_GH_VERSION_COMMAND}"
    ), "check-version must use the same version gate as the verifier steps"
    assert _GH_VERSION_COMMAND in step_script("publish-release", _PROVENANCE_STEP), (
        "the verifier step must still check the CLI that verifies attestations"
    )


def test_telemetry_steps_were_not_adopted() -> None:
    """git-donkey has no telemetry sink, so the workflow writes none."""
    used = {
        str(item.get("uses", ""))
        for job in jobs(WORKFLOW).values()
        for item in typ.cast("dict[str, typ.Any]", job).get("steps", [])
    }
    assert not any("release-telemetry" in action for action in used), (
        "the release workflow must not reference a telemetry action"
    )


def test_the_build_uses_the_validated_distribution_action() -> None:
    """The release publishes exactly the build every pull request validates."""
    found = step("build", "Build distributions")
    assert found["uses"] == "./.github/actions/pure-python-wheel", (
        "the release must reuse the pull-request build action"
    )
    assert found["with"] == {
        "python-version": "3.13",
        "artifact-name": "wheels-pure",
    }, "the action inputs must match the validated build"


def test_the_attest_job_requires_the_source_distribution() -> None:
    """Exactly one sdist and at least one wheel leave the build."""
    script = step_script("attest", "Collect release artefacts")
    for fragment in (
        "sdists=(dist/publish/*.tar.gz)",
        'if [ "${#sdists[@]}" -ne 1 ]; then',
        "Expected exactly one source distribution.",
        "wheels=(dist/publish/*.whl)",
        'if [ "${#wheels[@]}" -eq 0 ]; then',
        "Expected at least one wheel.",
    ):
        assert fragment in script, f"collection must enforce {fragment!r}"
    assert "${{" not in script, "the collection script must not interpolate expressions"


def test_the_published_artefacts_are_the_attested_ones() -> None:
    """Provenance covers every file, and later jobs ship exactly those bytes."""
    attest = _uses("attest", "actions/attest-build-provenance")
    assert attest["with"] == {"subject-path": "dist/publish/*"}, (
        "every staged distribution must be attested"
    )
    for name in ("publish-pypi", "draft-release", "publish-release"):
        assert "attest" in _needs(name), f"{name} must wait on the attestation"
        downloads = [
            item["with"]
            for item in _job(name)["steps"]
            if str(item.get("uses", "")).startswith("actions/download-artifact@")
        ]
        assert {"name": "release-dist", "path": "dist"} in downloads, (
            f"{name} must take the attested artefact, not rebuilt files"
        )


def test_carried_assets_are_verified_before_they_reach_the_pypi_job() -> None:
    """The draft snapshot hands policy-verified records to the token-free job."""
    draft_steps = _job("draft-release")["steps"]
    names = [item.get("name") for item in draft_steps]
    handoff_index = next(
        index
        for index, item in enumerate(draft_steps)
        if item.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert (
        names.index("Snapshot the release assets")
        < names.index("Verify carried asset provenance")
        < handoff_index
    ), "verification must precede the handoff"
    verifier = step_script("draft-release", "Verify carried asset provenance")
    for fragment in (
        "carryover_file=github-state/carryover.txt",
        'if [ ! -f "${carryover_file}" ]; then',
        'if [ ! -s "${carryover_file}" ]; then',
        'echo "verification_failures=0" >> "${GITHUB_OUTPUT}"',
        "required_version=2.102.0",
        "sort -V",
        '--repo "${GH_REPO}"',
        '--signer-workflow "${GH_REPO}/.github/workflows/release.yml"',
        '--source-ref "${GITHUB_REF}"',
        '--source-digest "${GITHUB_SHA}"',
        "--deny-self-hosted-runners",
        "--format json",
        'rm -f "${record}"',
        "verification_failures=$((verification_failures + 1))",
        '>> "${GITHUB_OUTPUT}"',
        "::warning title=attestation::",
    ):
        assert fragment in verifier, f"the verifier must enforce {fragment!r}"
    assert verifier.index('if [ ! -s "${carryover_file}" ]; then') < verifier.index(
        "required_version=2.102.0"
    ), "the CLI minimum applies only when carried assets need verification"
    assert "${{" not in verifier, (
        "the verifier script must not interpolate workflow expressions"
    )
    pending = step_script("publish-pypi", "Skip artefacts already on PyPI")
    assert "--provenance github-state/provenance" in pending, (
        "PyPI staging must receive the verifier records"
    )


def test_the_upload_keeps_pypi_attestations_and_is_idempotent() -> None:
    """PEP 740 attestations stay on, and a partial earlier upload is tolerated."""
    upload = _uses("publish-pypi", "pypa/gh-action-pypi-publish")
    assert upload["if"] == "steps.pending.outputs.remaining == 'true'", (
        "the upload must run only while an artefact remains"
    )
    assert upload["with"] == {
        "packages-dir": "dist/publish/",
        "skip-existing": True,
    }, "the publisher must skip an existing file instead of failing"


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
        'if [ "${covered}" != true ]',
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


def test_an_incomplete_published_release_returns_to_draft() -> None:
    """A visible release that is not complete is hidden before repair."""
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
        "gh release download",
        "--bundle",
        "bundle-coverage",
        'covered="$(grep',
        'if [ "${covered}" != true ]',
        "for attempt in 1 2 3",
        "::error title=bundle-verification::",
        "release-complete",
        "--index pypi-index.json",
        "--bundles release-bundles",
    ):
        assert fragment in script, f"the re-draft check must include {fragment!r}"
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


def test_nothing_is_ever_overwritten_and_pypi_holds_no_github_token() -> None:
    """No script clobbers an asset, and the PyPI job cannot touch GitHub."""
    scripts = [
        holder["run"]
        for _, holder in run_scripts(workflow_document(WORKFLOW), WORKFLOW)
    ]
    assert scripts, "the scan must reach the release scripts"
    assert not any("--clobber" in script for script in scripts), (
        "no release step may overwrite an existing asset"
    )
    assert "GH_TOKEN" not in str(_job("publish-pypi")), (
        "the job holding the PyPI token must not also hold a GitHub token"
    )


def test_every_scripts_checkout_is_sparse_and_credential_free() -> None:
    """The jobs that run ``scripts/`` fetch only that directory."""
    for name in ("draft-release", "publish-pypi", "publish-release"):
        checkout = _uses(name, "actions/checkout")
        assert checkout["with"] == {
            "persist-credentials": False,
            "sparse-checkout": "scripts\n",
        }, f"{name} must check out only the reconciliation scripts"


def test_every_release_script_starts_with_strict_shell_mode() -> None:
    """A failing command must stop the step that runs it."""
    scripts = [
        (where, holder["run"])
        for where, holder in run_scripts(workflow_document(WORKFLOW), WORKFLOW)
    ]
    assert scripts, "the scan must reach the release scripts"
    for where, script in scripts:
        assert script.startswith("set -euo pipefail\n"), (
            f"{where} must start with set -euo pipefail"
        )
