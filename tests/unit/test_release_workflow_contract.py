"""Structural contracts of the release workflow and of every ``run`` script.

These read the parsed workflow rather than executing it: which job holds which
token scope, what gates the upload, and which values may reach a shell. The
executed behaviour of the same steps lives in ``test_release_publish_steps.py``,
``test_release_github_steps.py``, and ``test_release_reconciliation.py``.
"""

from __future__ import annotations

import pathlib as pth
import re
import typing as typ

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from tests.release_workflow import (
    WORKFLOW,
    jobs,
    step,
    step_script,
    workflow_document,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_PINNED = re.compile(r"@[0-9a-f]{40}$")
_PYPROJECT_CHECK = "Check the pyproject.toml version against the tag"
_GH_VERSION_CHECK = "Check the GitHub CLI version"
_GH_VERSION_COMMAND = "gh --version | python3 scripts/release_gh_version.py"
_FETCH_INDEX = "Fetch the PyPI index"
_PROVENANCE_STEP = "Verify provenance of PyPI bytes"
_PROVENANCE_DIR = "pypi-provenance"
#: A floor, not the exact count, so adding a step does not require an edit.
_MIN_SCANNED_SCRIPTS = 20

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

#: The one reviewed template expansion inside a shell script. It renders only
#: `true` or `false`, and the shared CV-005 contract (`token.check-step`)
#: requires the secret to be read there rather than through an `env` value.
_CREDENTIAL_CHECK_COMMAND: typ.Final = (
    'echo "available=${{ secrets.CS_ACCESS_TOKEN != \'\' }}" >> "$GITHUB_OUTPUT"'
)
_ALLOWED_EXPANSIONS: typ.Final = frozenset({
    ("coverage-main.yml", "codescene-token", _CREDENTIAL_CHECK_COMMAND)
})


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
    assert _PINNED.search(found[0]["uses"]), f"{prefix} must be pinned by commit SHA"
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


def test_checkouts_do_not_persist_the_token() -> None:
    """A checked-out repository must not keep a usable token on disk."""
    checkouts = [
        item
        for job in jobs(WORKFLOW).values()
        for item in typ.cast("dict[str, typ.Any]", job).get("steps", [])
        if str(item.get("uses", "")).startswith("actions/checkout@")
    ]
    assert checkouts, "the version check must check the repository out"
    assert all(item["with"]["persist-credentials"] is False for item in checkouts), (
        "every checkout must disable persisted credentials"
    )


def test_every_remote_action_is_pinned_to_a_commit() -> None:
    """A floating tag could change what a release run executes."""
    root = pth.Path(__file__).resolve().parents[2]
    paths = [
        *sorted((root / ".github" / "workflows").glob("*.yml")),
        *sorted((root / ".github" / "actions").glob("*/action.yml")),
    ]
    steps = [item for path in paths for item in _declared_steps(_document(path))]
    remote = [
        str(item["uses"])
        for item in steps
        if "uses" in item and not str(item["uses"]).startswith("./")
    ]
    assert remote, "the scan must reach the repository's remote actions"
    assert all(_PINNED.search(action) for action in remote), (
        "every remote action must be pinned by commit SHA"
    )


def _document(path: pth.Path) -> dict[str, typ.Any]:
    """Parse one workflow or composite action file for the pin scan."""
    return typ.cast(
        "dict[str, typ.Any]", yaml.safe_load(path.read_text(encoding="utf-8"))
    )


def _declared_steps(document: dict[str, typ.Any]) -> list[dict[str, typ.Any]]:
    """Return every step a workflow or composite action declares."""
    jobs = typ.cast("dict[str, typ.Any]", document.get("jobs", {}))
    runs = typ.cast("dict[str, typ.Any]", document.get("runs", {}))
    step_lists = [
        *(
            typ.cast("dict[str, typ.Any]", job).get("steps", [])
            for job in jobs.values()
        ),
        runs.get("steps", []),
    ]
    return [
        typ.cast("dict[str, typ.Any]", item)
        for steps in step_lists
        for item in typ.cast("list[object]", steps)
    ]


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
        "both jobs must read the index through the same script"
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
    check = names.index("Check both destinations hold the same bytes")
    assert (
        fetched
        < provenance
        < upload
        < check
        < names.index("Publish the GitHub Release")
    ), (
        "provenance must precede the upload, and the digests must be compared "
        "after the upload and before the release"
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


def test_nothing_is_ever_overwritten_and_pypi_holds_no_github_token() -> None:
    """No script clobbers an asset, and the PyPI job cannot touch GitHub."""
    scripts = [holder["run"] for _, holder in _run_scripts(jobs(WORKFLOW), WORKFLOW)]
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
        for where, holder in _run_scripts(jobs(WORKFLOW), WORKFLOW)
    ]
    assert scripts, "the scan must reach the release scripts"
    for where, script in scripts:
        assert script.startswith("set -euo pipefail\n"), (
            f"{where} must start with set -euo pipefail"
        )


def _run_scripts(
    document: object, where: str
) -> cabc.Iterator[tuple[str, dict[str, typ.Any]]]:
    """Yield every mapping that declares a ``run`` script, with its location."""
    match document:
        case dict() as mapping:
            if isinstance(mapping.get("run"), str):
                yield where, typ.cast("dict[str, typ.Any]", mapping)
            for key, value in mapping.items():
                yield from _run_scripts(value, f"{where}.{key}")
        case list() as items:
            for index, value in enumerate(items):
                yield from _run_scripts(value, f"{where}[{index}]")


def _expansions_in(name: str, document: object) -> list[str]:
    """Return each ``run`` script location in ``document`` that expands ``${{``."""
    return [
        where
        for where, holder in _run_scripts(document, name)
        if "${{" in holder["run"]
        and (name, holder.get("id"), holder["run"].strip()) not in _ALLOWED_EXPANSIONS
    ]


def _document_name(path: pth.Path) -> str:
    """Name a workflow by file name and a composite action by its path."""
    if path.parent.name == "workflows":
        return path.name
    return path.relative_to(pth.Path(__file__).resolve().parents[2]).as_posix()


def _ci_documents() -> dict[str, object]:
    """Parse every workflow and composite action in the repository."""
    root = pth.Path(__file__).resolve().parents[2]
    paths = [
        *sorted((root / ".github" / "workflows").glob("*.yml")),
        *sorted((root / ".github" / "actions").glob("*/action.yml")),
    ]
    return {_document_name(path): _document(path) for path in paths}


def test_no_run_script_expands_a_template_expression() -> None:
    """Values reach a shell through ``env``, never by pasting into the script."""
    documents = _ci_documents()
    scanned = sum(
        len(list(_run_scripts(document, name))) for name, document in documents.items()
    )
    assert scanned > _MIN_SCANNED_SCRIPTS, (
        "the scan must reach the repository's run scripts"
    )
    assert ".github/actions/pure-python-wheel/action.yml" in documents, (
        "the scan must reach the distribution build action"
    )
    findings = [
        where
        for name, document in documents.items()
        for where in _expansions_in(name, document)
    ]
    assert not findings, f"route these values through env: {findings}"


@pytest.mark.parametrize(
    ("name", "document", "is_flagged"),
    [
        ("x.yml", {"steps": [{"run": 'echo "${{ inputs.target }}"'}]}, True),
        ("x.yml", {"runs": {"steps": [{"run": "make ${{ matrix.check }}"}]}}, True),
        (
            "x.yml",
            {"steps": [{"env": {"T": "${{ inputs.t }}"}, "run": 'echo "$T"'}]},
            False,
        ),
        (
            "x.yml",
            {"steps": [{"id": "codescene-token", "run": _CREDENTIAL_CHECK_COMMAND}]},
            True,
        ),
        (
            "coverage-main.yml",
            {"steps": [{"id": "codescene-token", "run": _CREDENTIAL_CHECK_COMMAND}]},
            False,
        ),
    ],
    ids=[
        "input",
        "composite-matrix",
        "through-env",
        "exception-elsewhere",
        "exception",
    ],
)
def test_the_expansion_scan_discriminates(
    name: str, document: object, *, is_flagged: bool
) -> None:
    """The scan flags pasted values and admits only the one reviewed exception."""
    assert bool(_expansions_in(name, document)) is is_flagged, (
        "the expansion scan must flag pasted values and admit the exception"
    )


_LEAVES = st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=4))
#: `run` often enough that holders, and non-string `run` values, both appear.
_KEYS = st.sampled_from(["run", "run", "steps", "with", "id"])
_DOCUMENTS = st.recursive(
    _LEAVES,
    lambda children: st.one_of(
        st.lists(children, max_size=4), st.dictionaries(_KEYS, children, max_size=4)
    ),
    max_leaves=30,
)


def _run_holders(document: object) -> list[int]:
    """Return the identity of every mapping with a string ``run``, iteratively."""
    found: list[int] = []
    pending = [document]
    while pending:
        node = pending.pop()
        match node:
            case dict() as mapping:
                if isinstance(mapping.get("run"), str):
                    found.append(id(mapping))
                pending.extend(mapping.values())
            case list() as items:
                pending.extend(items)
    return found


@given(document=_DOCUMENTS)
def test_the_run_script_scan_finds_exactly_the_run_holders(document: object) -> None:
    """The scan yields every mapping with a string ``run`` and nothing else."""
    scanned = list(_run_scripts(document, "doc"))

    assert sorted(id(holder) for _, holder in scanned) == sorted(
        _run_holders(document)
    ), "a missed holder is a script the expansion scan never reads"
    locations = [where for where, _ in scanned]
    assert len(set(locations)) == len(locations), "each holder has one location"
