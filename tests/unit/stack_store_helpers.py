"""Builders and Git-state readers shared by the ``stack_store`` suites.

The store's contract is mostly Git's own behaviour, so every suite runs against
a real temporary repository rather than a double for ``Repo``. Whether a
configuration subsection keeps the case and the punctuation of a branch name,
whether a ref update can be made create-only, and what deleting a branch does
to its configuration section are all facts about Git, so these helpers read the
repository back through ``git config``, ``git rev-parse``, and
``git for-each-ref`` rather than asking the store under test. A helper that
only one suite needs stays with that suite instead.

``test_stack_store.py`` covers the writer, which creates, refreshes, and
entombs a record; ``test_stack_store_clearing.py`` covers the sweep and the
prune, which clear what a deleted branch left behind; and
``test_stack_store_reads.py`` covers the answers the reader gives, which every
command relies on. A write Git refuses, and the repair that follows it, is in
``test_stack_store_refusals.py``, which keeps the lock files it plants to
itself.
"""

from __future__ import annotations

import re
import time
import typing as typ
from pathlib import Path

from syrupy.matchers import path_type

from git_donkey import stack_records, stack_writes
from tests import git_repo_helpers

if typ.TYPE_CHECKING:
    from git import Repo

# The trunk and the branch names the suites build around, named once so a
# failure in either suite points at the same branches.
TRUNK = "main"
PARENT = stack_records.StackParent(branch="parent", pull_request=None)
CHILD = "child"
NEIGHBOUR = "neighbour"
RENAMED = "renamed"

# The retention window a caller resolves before it reaches the store. The
# store takes whatever expiry expression it is handed, so the value here is
# the documented default rather than a constant the store owns.
EXPIRE = "90.days.ago"


def redact_object_ids(data: str, _: object) -> str:
    """Rewrite full object IDs so a snapshot does not pin a commit hash.

    Parameters
    ----------
    data : str
        The value the snapshot captured.
    _ : object
        The syrupy match, unused by this replacer.

    Returns
    -------
    str
        ``data`` with every 40-character hexadecimal object ID replaced by an
        ``<OID>`` placeholder. The seed commit's hash is derived partly from
        the clock, so a snapshot holding it raw would only ever match the run
        that recorded it.

    """
    return re.sub(r"\b[0-9a-f]{40}\b", "<OID>", data)


# Redact object IDs at record time, whatever path they sit at.
OID_MATCHER = path_type(types=(str,), replacer=redact_object_ids)


def make_repo(tmp_path: Path) -> Repo:
    """Return a repository holding one seed commit checked out on the trunk."""
    return git_repo_helpers.seed_repo(tmp_path / "repo", branch=TRUNK)


def make_writer(repo: Repo) -> stack_writes.GitStackRecordWriter:
    """Return a writer for ``repo``."""
    return stack_writes.GitStackRecordWriter(repo)


def make_record(
    branch: str, base: str, *, tip: str | None = None
) -> stack_records.StackRecord:
    """Return a record for ``branch`` whose exclusive boundary is ``base``."""
    return stack_records.StackRecord(
        branch=branch,
        parent=PARENT,
        base=base,
        recorded_from=base if tip is None else tip,
        evidence=stack_records.EVIDENCE_BIRTH,
    )


def advance(repo: Repo) -> str:
    """Commit an empty change on the trunk and return the new commit's ID."""
    return git_repo_helpers.advance(repo, message="Advance the trunk")


def delete_branch(repo: Repo, branch: str, *flags: str) -> None:
    """Delete ``branch`` through Git alone, bypassing the store entirely.

    ``git branch -d`` removes the branch's configuration section along with
    the branch, so the record survives only as its anchor.
    """
    repo.git.branch(*(flags or ("-d",)), branch)


def delete_ref_surgically(repo: Repo, branch: str) -> None:
    """Delete the branch ref alone, leaving its configuration section behind."""
    repo.git.update_ref("-d", f"refs/heads/{branch}")


def rename_branch(repo: Repo, branch: str, renamed: str) -> None:
    """Rename ``branch``, which carries its section and leaves its anchor."""
    repo.git.branch("-m", branch, renamed)


def commit_on(repo: Repo, branch: str) -> str:
    """Commit an empty change on ``branch`` and return the new commit's ID."""
    return git_repo_helpers.commit_on(repo, branch)


def branch_names_in(repo: Repo, namespace: str) -> set[str]:
    """Return every branch named by a ref under ``namespace``."""
    prefix = f"{namespace}/"
    output = repo.git.for_each_ref("--format=%(refname)", prefix)
    return {
        line[len(prefix) :] for line in output.splitlines() if line.startswith(prefix)
    }


def anchor(repo: Repo, branch: str) -> str | None:
    """Return the commit the anchor ref names, if the branch has one."""
    return git_repo_helpers.ref_value(repo, stack_records.base_ref_path(branch))


def branches(repo: Repo) -> set[str]:
    """Return every local branch name."""
    return {head.name for head in repo.heads}


def namespace_violations(repo: Repo) -> set[str]:
    """Return the records whose branch does not exist, which is INV-9 broken."""
    return branch_names_in(repo, stack_records.BASE_NAMESPACE) - branches(repo)


def tombstone_log(repo: Repo, branch: str) -> Path:
    """Return the path of ``branch``'s tombstone reflog."""
    ref = stack_records.tombstone_ref_path(branch)
    return Path(repo.git_dir) / "logs" / ref


def backdate_tombstone(repo: Repo, branch: str, *, days: int) -> None:
    """Rewrite a tombstone's reflog so the tombstone reads as ``days`` old."""
    tip = str(repo.git.rev_parse(stack_records.tombstone_ref_path(branch)))
    when = int(time.time()) - days * 24 * 60 * 60
    line = f"{tip} {tip} Test User <test@example.com> {when} +0000\tupdate-ref: seed\n"
    tombstone_log(repo, branch).write_text(line)
