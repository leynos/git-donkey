"""Property tests for the release helpers' broad input invariants.

The example tests pin named cases; these tests exercise the reconciliation
partition, bundle coverage, and version classification over generated inputs.
They use the Hypothesis dependency the repository already declares and touch
only pure functions and in-memory or temporary state.
"""

from __future__ import annotations

import itertools
import tempfile
from pathlib import Path

from hypothesis import example, given, settings
from hypothesis import strategies as st

from scripts.release_assets import Source, plan
from scripts.release_bundle import combined_coverage, uncovered
from scripts.release_gh_version import MINIMUM_VERSION, parse_version
from scripts.release_gh_version import main as gh_version_main
from scripts.release_version import is_prerelease
from tests.release_asset_provenance import bundle_line

_NAMES = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789._-", min_size=1, max_size=12
)
_DIGESTS = st.text(alphabet="0123456789abcdef", min_size=1, max_size=8)
_ASSET_MAPS = st.dictionaries(_NAMES, _DIGESTS, max_size=6)
_TRIPLES = st.tuples(
    st.integers(min_value=0, max_value=30),
    st.integers(min_value=0, max_value=30),
    st.integers(min_value=0, max_value=30),
)
_BOUNDARY_TRIPLES = st.one_of(
    st.tuples(st.just(2), st.just(102), st.integers(min_value=0, max_value=2)),
    _TRIPLES,
)


def _spell(version: tuple[int, int, int]) -> str:
    """Render one release number the way ``gh --version`` does."""
    return ".".join(str(part) for part in version)


@settings(max_examples=100)
@given(
    local=st.sets(_NAMES, max_size=8),
    on_pypi=st.sets(_NAMES, max_size=8),
    on_github=st.sets(_NAMES, max_size=8),
)
def test_plan_partitions_names_by_the_canonical_precedence(
    local: set[str],
    on_pypi: set[str],
    on_github: set[str],
) -> None:
    """Every name gets one source and each destination gets only its gaps."""
    decided = plan(local, on_pypi, on_github)

    assert set(decided.canonical) == local, "every local name must be planned"
    for name in local:
        expected = (
            Source.PYPI
            if name in on_pypi
            else Source.GITHUB
            if name in on_github
            else Source.LOCAL
        )
        assert decided.canonical[name] is expected, (
            "PyPI must win over GitHub, and GitHub over the local build"
        )
    assert decided.pypi_uploads == frozenset(local - on_pypi), (
        "PyPI must receive exactly the names it lacks"
    )
    assert decided.github_uploads == frozenset(local - on_github), (
        "GitHub must receive exactly the names it lacks"
    )


@settings(max_examples=100)
@given(assets=_ASSET_MAPS)
def test_one_matching_subject_covers_every_asset(assets: dict[str, str]) -> None:
    """A name and digest pair for every asset leaves nothing uncovered."""
    assert uncovered(assets, set(assets.items())) == [], (
        "exact subject pairs must cover every asset"
    )


@settings(max_examples=100)
@given(assets=st.dictionaries(_NAMES, _DIGESTS, min_size=1, max_size=6))
def test_dropping_one_subject_leaves_exactly_that_name_uncovered(
    assets: dict[str, str],
) -> None:
    """Coverage is not accumulated from a partial subject set."""
    name = min(assets)
    subjects = set(assets.items()) - {(name, assets[name])}

    assert uncovered(assets, subjects) == [name], (
        "only the asset whose pair was dropped may be uncovered"
    )


@settings(max_examples=50)
@given(assets=st.dictionaries(_NAMES, _DIGESTS, min_size=1, max_size=4))
def test_a_covering_combined_bundle_wins_over_a_partial_one(
    assets: dict[str, str],
) -> None:
    """The best single combined bundle decides coverage."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        covering = root / "covering.attestations.jsonl"
        covering.write_text(
            "\n".join(itertools.starmap(bundle_line, assets.items())) + "\n",
            encoding="utf-8",
        )
        partial_name = min(assets)
        partial = root / "partial.attestations.jsonl"
        partial.write_text(
            bundle_line(partial_name, assets[partial_name]) + "\n",
            encoding="utf-8",
        )

        assert combined_coverage(assets, [partial, covering]) == [], (
            "a bundle naming every pair must satisfy coverage"
        )


@settings(max_examples=100)
@given(release=_TRIPLES, pre=st.sampled_from(["", "a1", "b2", "rc3", "alpha1"]))
def test_a_local_label_never_changes_prerelease_status(
    release: tuple[int, int, int], pre: str
) -> None:
    """A ``+local`` label is outside the public version's classification."""
    version = f"{_spell(release)}{pre}"

    assert is_prerelease(f"{version}+local") == is_prerelease(version), (
        "a local label must not create or remove a pre-release"
    )


@settings(max_examples=100)
@given(
    release=_TRIPLES,
    pre=st.sampled_from(["", "a1", "rc3"]),
    post=st.sampled_from(["", ".post1"]),
    dev=st.sampled_from(["", ".dev0"]),
)
def test_pre_and_dev_labels_are_prereleases(
    release: tuple[int, int, int], pre: str, post: str, dev: str
) -> None:
    """A pre-release or development label decides the classification."""
    version = f"{_spell(release)}{pre}{post}{dev}"

    assert is_prerelease(version) is bool(pre or dev), (
        "only a pre-release or development label makes a pre-release"
    )


@settings(max_examples=100)
@given(version=_TRIPLES)
def test_parse_version_round_trips_the_release(
    version: tuple[int, int, int],
) -> None:
    """The parser returns exactly the numbers ``gh`` printed."""
    line = f"gh version {_spell(version)} (2026-01-01)"

    assert parse_version(line) == version, (
        "the parsed release must equal the printed release"
    )


@settings(max_examples=100)
@given(version=_BOUNDARY_TRIPLES)
@example(version=(2, 101, 9))
@example(version=(2, 102, 0))
@example(version=(2, 102, 1))
def test_the_minimum_boundary_accepts_exactly_the_supported_releases(
    version: tuple[int, int, int],
) -> None:
    """The CLI decision follows the same tuple order as the minimum."""
    line = f"gh version {_spell(version)} (2026-01-01)"

    assert (gh_version_main([line]) == 0) == (version >= MINIMUM_VERSION), (
        "only a release at or above the minimum may pass the check"
    )


@settings(max_examples=100)
@given(
    version=_TRIPLES,
    prefix=st.text(alphabet=" -*", max_size=8),
    suffix=st.text(alphabet=" -*", max_size=8),
)
def test_surrounding_text_does_not_change_the_parsed_release(
    version: tuple[int, int, int], prefix: str, suffix: str
) -> None:
    """Only the ``gh version`` field is significant to the parser."""
    line = f"{prefix}gh version {_spell(version)} {suffix}"

    assert parse_version(line) == version, (
        "padding around the version field must not change the release"
    )
