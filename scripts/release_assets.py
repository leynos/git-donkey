"""Reconcile release artefacts across PyPI and the GitHub Release.

PyPI bytes remain canonical. Carried GitHub bytes require approved provenance,
and so do PyPI bytes uploaded to the GitHub Release. Identical local bytes
remain, unverified differences stop staging, and each destination receives only
missing names.

The CLI imports this module in the PyPI-token job, so it stays
standard-library-only; workflow ``curl``/``gh`` steps own network access.
``scripts/release_github_upload.py`` assembles and checks the upload set the
GitHub Release receives.
"""

from __future__ import annotations

import dataclasses as dc
import enum
import hashlib
import json
import os
import re
import shutil
import typing as typ
import urllib.parse
from pathlib import Path

from scripts.release_provenance import ProvenanceRecordError, require_matching_record

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: The PEP 691 JSON simple index; relative file URLs resolve against it.
INDEX_URL = "https://pypi.org/simple/git-donkey/"

#: The suffix of a single-run Sigstore bundle.
BUNDLE_SUFFIX = ".sigstore.json"

#: The suffix of the combined attestation bundle attached to a release.
ATTESTATIONS_SUFFIX = ".attestations.jsonl"


def is_bundle(name: str) -> bool:
    """Return whether a release asset name denotes a provenance bundle.

    Parameters
    ----------
    name : str
        Release asset filename.

    Returns
    -------
    bool
        ``True`` for a single-run Sigstore bundle or the combined
        attestation bundle.

    Examples
    --------
    >>> is_bundle("git-donkey-v1.2.3-run7-1.attestations.jsonl")
    True
    """
    return name.endswith((BUNDLE_SUFFIX, ATTESTATIONS_SUFFIX))


def has_bundle(assets: cabc.Mapping[str, object]) -> bool:
    """Return whether a release holds the combined attestation bundle.

    A single-run ``.sigstore.json`` bundle is a provenance record, but it can
    cover only the run that produced it. Completion requires the combined
    bundle that is assembled from the store for every published distribution.

    Returns
    -------
    bool
        Whether a combined attestation bundle is attached.
    """
    return any(name.endswith(ATTESTATIONS_SUFFIX) for name in assets)


#: Artefact names this module will stage. Wheel and sdist names never need
#: more, and the bound keeps a name safe as a path and as a TSV field.
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
_DIGEST_PREFIX = "sha256:"


class ReleaseAssetError(Exception):
    """A release asset is missing, unsafe, untrusted, or holds unexpected bytes."""


class Source(enum.StrEnum):
    """Where one artefact name's canonical bytes come from."""

    PYPI = "pypi"
    GITHUB = "github"
    LOCAL = "local"


@dc.dataclass(frozen=True, slots=True)
class Plan:
    """The canonical source of each name and what each destination lacks."""

    canonical: cabc.Mapping[str, Source]
    pypi_uploads: frozenset[str]
    github_uploads: frozenset[str]


def plan(
    local: cabc.Iterable[str],
    on_pypi: cabc.Collection[str],
    on_github: cabc.Collection[str],
) -> Plan:
    """Choose each name's canonical bytes and the uploads each side needs.

    Parameters
    ----------
    local : collections.abc.Iterable[str]
        The artefact names this run built and attested.
    on_pypi : collections.abc.Collection[str]
        Names PyPI already holds.
    on_github : collections.abc.Collection[str]
        Names the tag's GitHub Release already holds, draft or published.

    Returns
    -------
    Plan
        PyPI takes precedence over GitHub, and GitHub over this run's bytes;
        each destination is sent exactly the names it lacks.

    Examples
    --------
    >>> plan(["g.whl"], set(), {"g.whl"}).canonical["g.whl"]
    <Source.GITHUB: 'github'>
    """
    names = frozenset(local)
    canonical = {
        name: Source.PYPI
        if name in on_pypi
        else Source.GITHUB
        if name in on_github
        else Source.LOCAL
        for name in names
    }
    return Plan(
        canonical=canonical,
        pypi_uploads=frozenset(name for name in names if name not in on_pypi),
        github_uploads=frozenset(name for name in names if name not in on_github),
    )


def mismatches(
    local: cabc.Iterable[str],
    pypi_digests: cabc.Mapping[str, str],
    github_digests: cabc.Mapping[str, str | None],
) -> list[str]:
    """Describe every name the two destinations do not hold identically.

    Parameters
    ----------
    local : collections.abc.Iterable[str]
        The artefact names the release must carry.
    pypi_digests : collections.abc.Mapping[str, str]
        PyPI's SHA-256 hex digest for each name it holds.
    github_digests : collections.abc.Mapping[str, str | None]
        The GitHub Release's SHA-256 hex digest per asset, ``None`` when
        GitHub reports none.

    Returns
    -------
    list[str]
        One message per missing or differing name; empty when they agree.

    Examples
    --------
    >>> mismatches(["g.whl"], {"g.whl": "ab"}, {"g.whl": "ab"})
    []
    """
    return [
        problem
        for name in sorted(set(local))
        if (problem := _mismatch(name, pypi_digests, github_digests))
    ]


def _mismatch(
    name: str,
    pypi_digests: cabc.Mapping[str, str],
    github_digests: cabc.Mapping[str, str | None],
) -> str | None:
    """Describe how one name differs between the destinations, if it does."""
    pypi, github = pypi_digests.get(name), github_digests.get(name)
    if pypi is None:
        return f"{name} is missing from PyPI"
    if github is None:
        return f"{name} is missing from the GitHub Release or has no digest"
    if pypi != github:
        return f"{name} differs: PyPI sha256 {pypi}, GitHub sha256 {github}"
    return None


def destination_disagreements(
    local: cabc.Iterable[str],
    pypi_digests: cabc.Mapping[str, str],
    github_digests: cabc.Mapping[str, str | None],
) -> list[str]:
    """Describe each name both destinations already hold with different bytes.

    A missing name is not a disagreement: it is what staging uploads. A name
    both destinations hold differently is irreversible once PyPI accepts a
    related upload, so staging must stop before it happens.

    Parameters
    ----------
    local : collections.abc.Iterable[str]
        The artefact names this run will publish.
    pypi_digests : collections.abc.Mapping[str, str]
        PyPI's SHA-256 hex digest for each name it holds.
    github_digests : collections.abc.Mapping[str, str | None]
        The GitHub Release's SHA-256 hex digest per asset, ``None`` when
        GitHub reports none.

    Returns
    -------
    list[str]
        One message per name the destinations hold differently or whose
        GitHub digest is unknown; empty when every shared name agrees.

    Examples
    --------
    >>> destination_disagreements(["g.whl"], {"g.whl": "ab"}, {"g.whl": "cd"})
    ['g.whl differs: PyPI sha256 ab, GitHub sha256 cd']
    """
    problems: list[str] = []
    for name in sorted(set(local)):
        if name not in pypi_digests or name not in github_digests:
            continue
        github = github_digests[name]
        if github is None:
            problems.append(f"{name} has no SHA-256 digest on the GitHub Release")
        elif pypi_digests[name] != github:
            problems.append(
                f"{name} differs: PyPI sha256 {pypi_digests[name]}, "
                f"GitHub sha256 {github}"
            )
    return problems


@dc.dataclass(frozen=True, slots=True)
class PypiFile:
    """One file the PyPI index lists."""

    sha256: str
    url: str


def read_pypi_index(path: Path) -> dict[str, PypiFile]:
    """Map each filename in a PEP 691 JSON index to its digest and URL."""
    document = json.loads(path.read_text(encoding="utf-8"))
    return {
        entry["filename"]: PypiFile(
            sha256=entry["hashes"]["sha256"].lower(),
            url=urllib.parse.urljoin(INDEX_URL, entry["url"]),
        )
        for entry in document.get("files", [])
    }


def _github_asset_documents(path: Path) -> list[dict[str, object]]:
    """Return the asset objects in a ``gh release view --json assets`` listing."""
    document = json.loads(path.read_text(encoding="utf-8"))
    return [asset for asset in document.get("assets", []) if isinstance(asset, dict)]


def incomplete_github_assets(path: Path) -> list[str]:
    """Return the names of assets whose upload state is not ``uploaded``.

    Parameters
    ----------
    path : pathlib.Path
        File holding ``gh release view --json assets`` output.

    Returns
    -------
    list[str]
        The names GitHub reports as incomplete, sorted.

    Examples
    --------
    >>> incomplete_github_assets(Path("assets.json"))
    []
    """
    return sorted(
        str(asset.get("name"))
        for asset in _github_asset_documents(path)
        if asset.get("state", "uploaded") != "uploaded"
    )


def read_github_assets(path: Path) -> dict[str, str | None]:
    """Map each uploaded asset in ``gh release view --json assets`` to its digest.

    An asset whose upload state is not ``uploaded`` is omitted, so a ``starter``
    left behind by a failed transfer is treated as missing. Reconciliation
    commands refuse such a listing through :func:`incomplete_github_assets`;
    the destination metadata check treats it as a confirmed incomplete release.

    Returns
    -------
    dict[str, str | None]
        Each uploaded asset's SHA-256 hex digest, or ``None`` when GitHub
        reports no ``sha256:`` digest.
    """
    return {
        str(asset["name"]): _digest_hex(asset.get("digest"))
        for asset in _github_asset_documents(path)
        if asset.get("state", "uploaded") == "uploaded"
    }


def _digest_hex(digest: object) -> str | None:
    """Return the hex of a ``sha256:`` digest; ``None`` for any other form."""
    if isinstance(digest, str) and digest.startswith(_DIGEST_PREFIX):
        return digest.removeprefix(_DIGEST_PREFIX).lower()
    return None


def sha256_of(path: Path) -> str:
    """Return the SHA-256 hex digest of one file's bytes."""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def local_names(directory: Path) -> list[str]:
    """List the artefact names in ``directory``, refusing any unsafe name."""
    names = sorted(path.name for path in directory.iterdir())
    unsafe = [name for name in names if not _SAFE_NAME.fullmatch(name)]
    if unsafe:
        msg = f"refusing unsafe artefact names: {unsafe}"
        raise ReleaseAssetError(msg)
    return names


def require_digest(path: Path, expected: str | None) -> None:
    """Raise unless ``path`` holds bytes with the ``expected`` digest.

    Parameters
    ----------
    path : pathlib.Path
        File whose bytes are hashed.
    expected : str | None
        Expected SHA-256 hex digest; ``None`` never matches, because a
        destination that reports no digest establishes nothing.

    Raises
    ------
    ReleaseAssetError
        When the file's digest differs from ``expected``.
    """
    actual = sha256_of(path)
    if expected is None or actual != expected:
        msg = f"{path.name} has sha256 {actual}, expected {expected}"
        raise ReleaseAssetError(msg)


def _carried_asset_error(name: str, reason: str) -> ReleaseAssetError:
    """Build an actionable error for an untrusted carried asset."""
    return ReleaseAssetError(
        f"cannot safely carry GitHub asset {name} to PyPI: {reason}; "
        "rerun once; if still untrusted, delete the named asset and rerun"
    )


def _require_github_digest(name: str, digest: str | None) -> str:
    """Require the release API to report a SHA-256 digest for ``name``."""
    if digest is None:
        raise _carried_asset_error(name, "GitHub reports no SHA-256 digest")
    return digest


def _verify_carried_asset(
    asset: Path, provenance: Path, name: str, expected: str
) -> None:
    """Validate carried bytes and provenance, with an actionable error."""
    try:
        require_digest(asset, expected)
        require_matching_record(provenance / f"{name}.json", name, expected)
    except OSError as error:
        reason = "cannot read the downloaded GitHub bytes"
        raise _carried_asset_error(name, reason) from error
    except (ReleaseAssetError, ProvenanceRecordError) as error:
        raise _carried_asset_error(name, str(error)) from error


def write_output(name: str, *, value: bool) -> None:
    """Append one ``true``/``false`` step output for the workflow."""
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"{name}={str(value).lower()}\n")


@dc.dataclass(frozen=True, slots=True)
class State:
    """The run's artefacts and what each destination already holds."""

    publish: Path
    pypi: dict[str, PypiFile]
    github: dict[str, str | None]

    @classmethod
    def load(cls, publish: Path, index: Path | None, assets: Path) -> State:
        """Read the staged artefacts, the PyPI index, and the asset listing."""
        pypi = read_pypi_index(index) if index is not None else {}
        return cls(publish, pypi, read_github_assets(assets))

    def plan(self) -> Plan:
        """Plan this run's uploads against both destinations."""
        return plan(local_names(self.publish), self.pypi, self.github)


def carryover(state: State) -> list[str]:
    """Return the names whose GitHub bytes PyPI may need."""
    return sorted(set(local_names(state.publish)) & set(state.github))


def _prepare_carried_asset(
    state: State, name: str, github_files: Path, provenance: Path
) -> Path | None:
    """Validate a carried asset, or return ``None`` when local bytes match."""
    if not _SAFE_NAME.fullmatch(name):
        msg = f"refusing unsafe GitHub asset name: {name!r}"
        raise ReleaseAssetError(msg)

    artefact = state.publish / name
    expected = _require_github_digest(name, state.github[name])
    if sha256_of(artefact) == expected:
        return None
    asset = github_files / name
    _verify_carried_asset(asset, provenance, name, expected)
    return asset


def _apply_pypi_choice(
    name: str,
    source: Source,
    artefact: Path,
    carried: cabc.Mapping[str, Path],
) -> None:
    """Apply one already-validated source choice to the publish directory."""
    if source is Source.PYPI:
        print(f"::notice::Skipping {name}: already on PyPI")
        artefact.unlink()
    elif source is Source.GITHUB:
        asset = carried.get(name)
        if asset is None:
            print(f"::notice::Retaining this run's attested bytes for {name}")
        else:
            print(f"::notice::Uploading verified GitHub Release bytes for {name}")
            shutil.copyfile(asset, artefact)


def _require_destinations_agree(state: State) -> None:
    """Stop before an upload can make a disagreement irreversible."""
    pypi = {name: file.sha256 for name, file in state.pypi.items()}
    problems = destination_disagreements(local_names(state.publish), pypi, state.github)
    if problems:
        msg = "; ".join(problems)
        raise ReleaseAssetError(msg)


def stage_pypi(state: State, github_files: Path, provenance: Path) -> bool:
    """Leave in ``publish`` exactly the canonical bytes PyPI lacks.

    Parameters
    ----------
    state : State
        The staged artefacts and both destinations' holdings.
    github_files : pathlib.Path
        The GitHub Release assets ``draft-release`` downloaded.
    provenance : pathlib.Path
        The verifier's JSON results for carried GitHub assets.

    Returns
    -------
    bool
        Whether any artefact remains to upload.
    """
    _require_destinations_agree(state)
    decided = state.plan()
    carried: dict[str, Path] = {}
    for name, source in sorted(decided.canonical.items()):
        if source is Source.GITHUB:
            asset = _prepare_carried_asset(state, name, github_files, provenance)
            if asset is not None:
                carried[name] = asset

    for name, source in sorted(decided.canonical.items()):
        _apply_pypi_choice(name, source, state.publish / name, carried)
    if not decided.pypi_uploads:
        print("Every artefact is already on PyPI; nothing to publish.")
    return bool(decided.pypi_uploads)


def expected_bundle_problems(
    github_digests: cabc.Mapping[str, str | None],
    name: str,
    digest: str | None,
) -> list[str]:
    """Describe why the verified bundle is not the release's current asset.

    Parameters
    ----------
    github_digests : collections.abc.Mapping[str, str | None]
        The release's assets and their SHA-256 hex digests.
    name : str
        The bundle this run verified and expects to find attached.
    digest : str | None
        The SHA-256 hex digest recorded when the bundle was verified.

    Returns
    -------
    list[str]
        One message when the bundle is missing, has no reported digest, or
        differs; empty when the release holds exactly the verified bundle.

    Examples
    --------
    >>> expected_bundle_problems({"bundle": "ab"}, "bundle", "ab")
    []
    """
    if digest is None:
        return [f"no digest was recorded for the verified bundle {name}"]
    actual = github_digests.get(name)
    if actual is None:
        return [f"{name} is missing from the GitHub Release or has no digest"]
    if actual != digest:
        return [f"{name} differs: expected sha256 {digest}, GitHub sha256 {actual}"]
    return []


def verify(state: State, staged: Path) -> list[str]:
    """Describe every artefact or bundle the two destinations disagree on.

    Parameters
    ----------
    state : State
        The run's artefacts and both destinations' holdings.
    staged : pathlib.Path
        The upload directory prepared for this run, holding any bundle that
        was staged for upload.

    Returns
    -------
    list[str]
        One message per missing or differing name; empty when both
        destinations agree and the release carries a provenance bundle.
    """
    local = local_names(state.publish)
    pypi = {name: file.sha256 for name, file in state.pypi.items()}
    problems = mismatches(local, pypi, state.github)
    problems.extend(
        f"{name} is on the GitHub Release but not part of this run's build"
        for name in sorted(set(state.github) - set(local))
        if not is_bundle(name)
    )
    problems.extend(
        f"{path.name} is missing from the GitHub Release"
        for path in sorted(staged.iterdir())
        if is_bundle(path.name) and path.name not in state.github
    )
    if not has_bundle(state.github):
        problems.append("the GitHub Release holds no provenance bundle")
    return problems
