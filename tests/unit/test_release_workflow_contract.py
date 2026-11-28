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
