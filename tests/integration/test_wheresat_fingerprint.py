"""The fingerprint INV-1 is measured with, and the edits that calibrate it.

``test_wheresat_read_only.py`` compares two readings of a whole repository and
claims they agree. This module is what stops that claim from being a statement
about two readings of anything: every edit below is aimed at one reading of
:class:`~tests.integration.wheresat_helpers.Fingerprint` — the refs and the
commits they name, the index, the working tree, the stash, the local
configuration, ``FETCH_HEAD`` — and is required to move it, so a reading that
had stopped observing its part of the repository would fail here rather than let
the matrix pass over the hole.

The one difference INV-1 permits, a ref under ``refs/wheresat/``, is calibrated
the same way: the evidence ref is asserted to have been written before the
comparison is asked to let it through, so the allowance is known to be reachable
rather than vacuous.

The edits are made to a small repository of this module's own, built per test,
because the matrix's checkout is one no vector may disturb.
"""

from __future__ import annotations

import dataclasses
import typing as typ
from pathlib import Path

import pytest
from git import Repo

from git_donkey.wheresat_refs import per_run_ref
from tests import git_repo_helpers
from tests.integration.wheresat_helpers import fingerprint

if typ.TYPE_CHECKING:
    import collections.abc as cabc

pytestmark = pytest.mark.timeout(120)

_TRACKED: typ.Final = "README.md"
"""Tracked file the edits are made to."""

_FETCH_HEAD: typ.Final = "FETCH_HEAD"
"""File a fetch writes, which the edits write the way a fetch would."""

type _Edit = cabc.Callable[[Path, Repo], None]
"""A deliberate change to a repository, and to its working tree."""


@dataclasses.dataclass(frozen=True, slots=True)
class Change:
    """One deliberate edit, and the fingerprint reading it has to move.

    Attributes
    ----------
    label : str
        Name of the edit, which is how a failing example identifies itself.
    reading : str
        Reading of :class:`~tests.integration.wheresat_helpers.Fingerprint` the
        edit must be visible in.
    edit : _Edit
        The edit itself, taking a working tree and its repository.

    """

    label: str
    reading: str
    edit: _Edit


def _change_a_ref(root: Path, repo: Repo) -> None:
    """Create a ref, which is what retaining evidence looks like."""
    repo.git.branch("probe", repo.head.commit.hexsha)


def _change_a_tracked_file(root: Path, repo: Repo) -> None:
    """Rewrite the contents of a tracked file."""
    (root / _TRACKED).write_text("changed")


def _remove_a_tracked_file(root: Path, repo: Repo) -> None:
    """Delete a tracked file, which the status and the file listing both report."""
    (root / _TRACKED).unlink()


def _change_the_configuration(root: Path, repo: Repo) -> None:
    """Set a key in a repository's local configuration."""
    repo.git.config("--local", "wheresat.probe", "1")


def _change_the_fetch_head(root: Path, repo: Repo) -> None:
    """Write ``FETCH_HEAD`` the way a fetch would."""
    path = Path(repo.git.rev_parse("--absolute-git-dir")) / _FETCH_HEAD
    path.write_text(f"{repo.head.commit.hexsha}\t\tbranch 'main' of probe\n")


def _change_the_stash(root: Path, repo: Repo) -> None:
    """Stash a change, which leaves the stash ref behind."""
    _change_a_tracked_file(root, repo)
    repo.git.stash()


_CHANGES: typ.Final[cabc.Mapping[str, Change]] = {
    "ref": Change("ref", "refs", _change_a_ref),
    "file": Change("file", "files", _change_a_tracked_file),
    "status": Change("status", "status", _remove_a_tracked_file),
    "config": Change("config", "config", _change_the_configuration),
    "fetch-head": Change("fetch-head", "fetch-head", _change_the_fetch_head),
    "stash": Change("stash", "stashes", _change_the_stash),
}
"""Every reading, paired with an edit that has to be visible in it."""


@pytest.fixture
def mutable(tmp_path: Path) -> tuple[Path, Repo]:
    """Return a small repository and its working tree, for the control."""
    root = tmp_path / "mutable"
    return root, git_repo_helpers.seed_repo(root)


@pytest.mark.parametrize("label", tuple(_CHANGES))
def test_the_fingerprint_notices_a_change(
    label: str,
    mutable: tuple[Path, Repo],
) -> None:
    """Each edit moves the reading it is aimed at, so the fingerprint is sensitive.

    Without this control the matrix's claim is only that two readings agreed,
    which two readings of anything would. Each edit is aimed at one reading, and
    a reading that did not move is a hole in the measurement exactly where a
    later milestone's change would slip through.
    """
    root, repo = mutable
    change = _CHANGES[label]
    before = fingerprint(root, repo=repo)
    change.edit(root, repo)
    differences = before.differences(fingerprint(root, repo=repo))

    assert any(
        difference.startswith(f"{change.reading}: ") for difference in differences
    ), f"expected {label} to move the {change.reading} reading, got {differences}"


def test_evidence_a_run_may_write_is_not_a_difference(
    mutable: tuple[Path, Repo],
) -> None:
    """A ref under the evidence namespace is allowed, and really is written.

    INV-1 permits a run to retain evidence of its own, so the comparison has to
    let the namespace through — and the ref is asserted to have been written, so
    the allowance is known to be reachable rather than vacuous.
    """
    root, repo = mutable
    before = fingerprint(root, repo=repo)
    repo.git.update_ref(per_run_ref("probe", "boundary"), repo.head.commit.hexsha)
    after = fingerprint(root, repo=repo)

    assert after.refs != before.refs, "expected the evidence ref to be written"
    assert not before.differences(after), (
        "writing the evidence ref moved nothing else in the repository"
    )
