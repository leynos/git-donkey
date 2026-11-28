"""Stand-ins for ``gh`` and ``curl`` that hold release state in directories.

``release.yml``'s reconciliation steps talk to two destinations: the GitHub
Release through ``gh`` and PyPI through ``curl``. The tests replace both with
this program, run as ``python release_fake_tools.py gh|curl ARGS...``, so the
checked-in step scripts execute unchanged against state the test controls:

- ``FAKE_GITHUB_ASSETS``: a directory holding one file per release asset.
- ``FAKE_GITHUB_RELEASE_STATE``: the release's draft and pre-release flags.
- ``FAKE_PYPI_FILES``: a directory holding one file per PyPI file.
- ``FAKE_ATTESTATIONS``: JSON records for repository build attestations. Each
  carries a subject name and digest plus the repository, signer workflow,
  source ref, source digest, predicate type, and runner kind that
  ``gh attestation verify`` must match.
- ``FAKE_GITHUB_DIGEST_OVERRIDES``: JSON filename-to-SHA-256 hex metadata
  overrides, without the ``sha256:`` prefix.
- ``FAKE_CALLS``: a file each call appends its argument vector to, as JSON.
- ``FAKE_CURL_STATUSES``: comma-separated HTTP statuses the index returns in
  turn; the last repeats. Defaults to ``200``.
- ``FAKE_ATTEST_FAIL``: optional stderr text that makes verification fail.
- ``FAKE_ATTEST_FAIL_COUNT``: number of initial verification attempts to fail.
- ``FAKE_GH_VERSION``: the fake GitHub CLI version; defaults to ``2.102.0``.

Like the real services, neither stand-in ever overwrites a file it holds: a
second upload of an existing asset name fails, as ``gh`` does without
``--clobber``.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import typing as typ
from fnmatch import fnmatch
from pathlib import Path

from scripts.release_bundle import read_bundle_subjects
from tests.release_asset_provenance import bundle_line, sha256_hex

#: Where the fake PyPI index says each file can be downloaded from.
FILES_URL = "https://files.example.test/packages/"
_INDEX_URL = "https://pypi.org/simple/git-donkey/"
_MIN_RELEASE_ARGUMENTS = 2
_MIN_ATTESTATION_ARGUMENTS = 3


def _directory(variable: str) -> Path:
    """Return the state directory named by ``variable``, creating it."""
    directory = Path(os.environ[variable])
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _record(tool: str, args: list[str]) -> None:
    """Append one call to the shared call log."""
    with Path(os.environ["FAKE_CALLS"]).open("a", encoding="utf-8") as calls:
        calls.write(json.dumps([tool, *args]) + "\n")


def _assets_json() -> str:
    """Render the release's assets as ``gh release view --json assets`` does."""
    overrides = typ.cast(
        "dict[str, str]",
        json.loads(os.environ.get("FAKE_GITHUB_DIGEST_OVERRIDES", "{}")),
    )
    states = typ.cast(
        "dict[str, str]",
        json.loads(os.environ.get("FAKE_GITHUB_ASSET_STATES", "{}")),
    )
    assets = [
        {
            "name": path.name,
            "digest": (
                f"sha256:{overrides.get(path.name, sha256_hex(path.read_bytes()))}"
            ),
            "state": states.get(path.name, "uploaded"),
        }
        for path in sorted(_directory("FAKE_GITHUB_ASSETS").iterdir())
    ]
    return json.dumps({"assets": assets})


def _state_path() -> Path:
    """Return the file holding the fake release's draft state."""
    return Path(os.environ["FAKE_GITHUB_RELEASE_STATE"])


def _release_state() -> dict[str, object] | None:
    """Read the fake release state, or ``None`` when no release exists."""
    path = _state_path()
    if not path.exists():
        return None
    return typ.cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))


def _write_release_state(state: dict[str, object]) -> None:
    """Persist the fake release state."""
    _state_path().write_text(json.dumps(state), encoding="utf-8")


def _gh_upload(files: list[str]) -> int:
    """Attach ``files``, refusing any name the release already holds."""
    assets = _directory("FAKE_GITHUB_ASSETS")
    if "--clobber" in files:
        print("the fake release refuses --clobber", file=sys.stderr)
        return 1
    for name in files:
        target = assets / Path(name).name
        if target.exists():
            print(f"asset {target.name} already exists", file=sys.stderr)
            return 1
        shutil.copyfile(name, target)
    return 0


def _gh_download(args: list[str]) -> int:
    """Copy the assets ``--pattern`` matches into ``--dir``."""
    pattern = args[args.index("--pattern") + 1]
    target = Path(args[args.index("--dir") + 1])
    failed = os.environ.get("FAKE_DOWNLOAD_FAIL", "")
    if failed and fnmatch(pattern, failed):
        print(f"controlled download failure for {pattern}", file=sys.stderr)
        return 1
    assets = _directory("FAKE_GITHUB_ASSETS")
    matches = sorted(path for path in assets.iterdir() if fnmatch(path.name, pattern))
    if not matches:
        print(f"asset {pattern} not found", file=sys.stderr)
        return 1
    target.mkdir(parents=True, exist_ok=True)
    for source in matches:
        shutil.copyfile(source, target / source.name)
    return 0


def _option(args: list[str], name: str) -> str | None:
    """Return the value following one option, if both are present."""
    try:
        position = args.index(name)
    except ValueError:
        return None
    return args[position + 1] if position + 1 < len(args) else None


def _record_for_digest(record: object, digest: str) -> dict[str, object] | None:
    """Return a record for ``digest``, if the decoded value is one."""
    match record:
        case {"sha256": str() as record_digest} if record_digest.lower() == digest:
            # JSON object keys are strings, though a pattern cannot express that.
            return typ.cast("dict[str, object]", record)
        case _:
            return None


def _attestations_for_digest(
    records_path: Path, digest: str
) -> list[dict[str, object]]:
    """Read attestation records and keep entries for the asset digest."""
    match json.loads(records_path.read_text(encoding="utf-8")):
        case list() as document:
            return [
                record
                for value in document
                if (record := _record_for_digest(value, digest)) is not None
            ]
        case _:
            return []


def _matches_attestation_policy(
    record: dict[str, object],
    args: list[str],
) -> bool:
    """Return whether a record satisfies each requested identity constraint.

    The repository, signer workflow and source ref are always compared, so a
    verifier call that omits one cannot match a record that carries it. The
    source digest and predicate type are compared only when the verifier asks
    for them: ``draft-release`` relies on the omission of ``--source-digest``
    to keep an interrupted attempt for the same tag eligible.

    Returns
    -------
    bool
        Whether the record satisfies every constraint the call supplied.
    """
    if record.get("repository") != _option(args, "--repo"):
        return False
    if record.get("signer_workflow") != _option(args, "--signer-workflow"):
        return False
    if record.get("source_ref") != _option(args, "--source-ref"):
        return False
    for option, field in (
        ("--source-digest", "source_digest"),
        ("--predicate-type", "predicate_type"),
    ):
        requested = _option(args, option)
        if requested is not None and record.get(field) != requested:
            return False
    return (
        "--deny-self-hosted-runners" not in args or record.get("self_hosted") is False
    )


def _attestations_for_policy(
    records: list[dict[str, object]], args: list[str]
) -> list[dict[str, object]]:
    """Keep digest matches that satisfy the verifier's identity options."""
    return [record for record in records if _matches_attestation_policy(record, args)]


def _verification_output(records: list[dict[str, object]]) -> str:
    """Render matching records in the fake verifier's JSON output shape."""
    result = [
        {
            "verificationResult": {
                "statement": {
                    "subject": [
                        {
                            "name": record.get("subject_name"),
                            "digest": {"sha256": record["sha256"]},
                        }
                    ]
                }
            }
        }
        for record in records
    ]
    return json.dumps(result)


def _gh_attestation_download(args: list[str]) -> int:
    """Write the fake attestation records for one artefact as a JSONL bundle."""
    asset = Path(args[2])
    digest = sha256_hex(asset.read_bytes())
    records = _attestations_for_digest(Path(os.environ["FAKE_ATTESTATIONS"]), digest)
    if not records:
        print(f"No attestations found for sha256 {digest}", file=sys.stderr)
        return 1
    subject = os.environ.get("FAKE_ATTEST_DOWNLOAD_SUBJECT") or None
    target = Path(f"sha256-{digest}.jsonl")
    target.write_text(
        "\n".join(
            bundle_line(
                subject or str(record.get("subject_name")), str(record["sha256"])
            )
            for record in records
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


def _gh_attestation_verify(args: list[str]) -> int:
    """Verify fake digest records against the workflow's identity policy."""
    if failure := os.environ.get("FAKE_ATTEST_FAIL"):
        print(failure, file=sys.stderr)
        return 1

    counter = Path(os.environ["FAKE_CALLS"]).with_suffix(".attestation-count")
    attempt = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
    counter.write_text(str(attempt + 1), encoding="utf-8")
    fail_count = int(os.environ.get("FAKE_ATTEST_FAIL_COUNT", "0"))
    if attempt < fail_count:
        print(f"controlled verifier failure on attempt {attempt + 1}", file=sys.stderr)
        return 1

    asset = Path(args[2])
    digest = sha256_hex(asset.read_bytes())
    bundle = _option(args, "--bundle")
    if bundle is not None and (asset.name, digest) not in read_bundle_subjects([
        Path(bundle)
    ]):
        print(f"No matching bundle subject for {asset.name}", file=sys.stderr)
        return 1
    records_path = Path(os.environ["FAKE_ATTESTATIONS"])
    digest_matches = _attestations_for_digest(records_path, digest)
    if not digest_matches:
        print(f"No attestations found for sha256 {digest}", file=sys.stderr)
        return 1

    verified = _attestations_for_policy(digest_matches, args)
    if not verified:
        print("No attestations satisfy the verification policy", file=sys.stderr)
        return 1

    print(_verification_output(verified))
    return 0


def _gh_release_view(args: list[str]) -> int:
    """Serve the release existence check and the two JSON views."""
    state = _release_state()
    if state is None:
        print("release not found", file=sys.stderr)
        return 1
    fields = _option(args, "--json")
    match fields:
        case "assets":
            print(_assets_json())
        case "isDraft,assets":
            print(
                json.dumps({
                    "isDraft": state.get("isDraft"),
                    "assets": json.loads(_assets_json())["assets"],
                })
            )
        case "isDraft,isPrerelease":
            print(
                json.dumps({
                    "isDraft": state.get("isDraft"),
                    "isPrerelease": state.get("isPrerelease"),
                })
            )
        case _:
            print(json.dumps(state))
    return 0


def _gh_release_create(args: list[str]) -> int:
    """Create the release as a draft with the requested pre-release flag."""
    _write_release_state({
        "isDraft": True,
        "isPrerelease": "--prerelease" in args,
    })
    return 0


def _gh_release_edit(args: list[str]) -> int:
    """Apply the draft and pre-release flags an edit requests."""
    state = _release_state() or {}
    for argument in args:
        if argument == "--draft=false":
            state["isDraft"] = False
        elif argument == "--draft=true":
            state["isDraft"] = True
        elif argument.startswith("--prerelease="):
            state["isPrerelease"] = argument.split("=", 1)[1] == "true"
    _write_release_state(state)
    return 0


def _gh_delete_asset(args: list[str]) -> int:
    """Delete the named asset from the fake release."""
    target = _directory("FAKE_GITHUB_ASSETS") / args[0]
    if not target.exists():
        print(f"asset {args[0]} not found", file=sys.stderr)
        return 1
    target.unlink()
    return 0


def _gh_release(args: list[str]) -> int:
    """Serve the supported release subcommands."""
    handlers = {
        "view": _gh_release_view,
        "create": _gh_release_create,
        "edit": _gh_release_edit,
        "download": _gh_download,
        "upload": lambda call: _gh_upload(call[3:]),
        "delete-asset": lambda call: _gh_delete_asset(call[3:]),
    }
    handler = handlers.get(args[1])
    if handler is None:
        return _unsupported_gh_operation(args[:2])
    return handler(args)


def _unsupported_gh_operation(args: list[str]) -> int:
    """Reject an operation the checked-in release workflow does not use."""
    operation = " ".join(args)
    print(f"unsupported fake gh operation: {operation}", file=sys.stderr)
    return 2


def gh(args: list[str]) -> int:
    """Serve only the GitHub CLI operations the release workflow uses."""
    if args == ["--version"]:
        print(f"gh version {os.environ.get('FAKE_GH_VERSION', '2.102.0')} (fake)")
        return 0
    if (
        args[:2] == ["attestation", "verify"]
        and len(args) >= _MIN_ATTESTATION_ARGUMENTS
    ):
        return _gh_attestation_verify(args)
    if (
        args[:2] == ["attestation", "download"]
        and len(args) >= _MIN_ATTESTATION_ARGUMENTS
    ):
        return _gh_attestation_download(args)
    if len(args) < _MIN_RELEASE_ARGUMENTS or args[0] != "release":
        return _unsupported_gh_operation(args)
    return _gh_release(args)


def _next_status() -> str:
    """Return the next scripted index status; the last one repeats."""
    statuses = os.environ.get("FAKE_CURL_STATUSES", "200").split(",")
    counter = Path(os.environ["FAKE_CALLS"]).with_suffix(".index-count")
    served = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
    counter.write_text(str(served + 1), encoding="utf-8")
    return statuses[min(served, len(statuses) - 1)]


def _index_json() -> str:
    """Render the PyPI state as a PEP 691 JSON simple index."""
    files = [
        {
            "filename": path.name,
            "url": f"{FILES_URL}{path.name}",
            "hashes": {"sha256": sha256_hex(path.read_bytes())},
        }
        for path in sorted(_directory("FAKE_PYPI_FILES").iterdir())
    ]
    return json.dumps({"name": "git-donkey", "files": files})


def curl(args: list[str]) -> int:
    """Serve the index or one file, writing ``%{http_code}`` if asked."""
    url, output = args[-1], Path(args[args.index("--output") + 1])
    if url == _INDEX_URL:
        status = _next_status()
        output.write_text(_index_json() if status == "200" else "{}", encoding="utf-8")
        print(status, end="")
        # curl exits non-zero, still writing `000`, when nothing answered.
        return 7 if status == "000" else 0
    source = _directory("FAKE_PYPI_FILES") / url.removeprefix(FILES_URL)
    if not source.exists():
        return 22
    shutil.copyfile(source, output)
    return 0


def main(argv: list[str]) -> int:
    """Dispatch to the stand-in named by the first argument."""
    tool, args = argv[0], argv[1:]
    _record(tool, args)
    return gh(args) if tool == "gh" else curl(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
