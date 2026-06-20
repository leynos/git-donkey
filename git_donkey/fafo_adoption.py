"""Remote adoption checks for existing ``git-fafo`` repositories.

The orchestration in ``git_donkey.fafo`` delegates existing-remote validation
to this module before any local scaffold is created. The helpers classify the
advertised Git refs and reject anything beyond a truly empty remote or a
single empty initial commit, which keeps trusted Copier tasks from running
against repositories that cannot be adopted.
"""

from __future__ import annotations

import dataclasses
import logging
import tempfile
import typing as typ

from plumbum import local
from plumbum.commands.processes import ProcessExecutionError

from git_donkey import helpers

_GIT_FAFO_PREFIX = "git-fafo"
_LOGGER = logging.getLogger(__name__)


class _GitCommand(typ.Protocol):
    """Minimal plumbum command protocol used by Git helpers."""

    def __getitem__(self, args: object) -> _GitCommand:
        """Return a command with additional arguments bound."""
        ...

    def __call__(self) -> str:
        """Run the command and return stdout."""
        ...


@dataclasses.dataclass(frozen=True)
class _RemoteState:
    """Adoption-relevant view of an existing remote.

    ``branch`` names the single existing head when the remote holds nothing but
    an empty initial commit; it is ``None`` when the remote has no refs at all.
    ``adoptable`` is ``False`` whenever the remote carries real content.
    ``reason`` records the classification cause for logs, diagnostics, and
    future metrics export.
    """

    adoptable: bool
    branch: str | None = None
    reason: str = "adoptable"


def _remote_heads_and_tags(git: _GitCommand) -> tuple[list[str], list[str]]:
    """Return the head and tag names advertised by the ``origin`` remote."""
    output = git["ls-remote", "origin"]()
    return _heads_and_tags_from_ls_remote(output)


def _heads_and_tags_from_ls_remote(output: str) -> tuple[list[str], list[str]]:
    """Parse ``git ls-remote`` output into branch heads and tags."""
    heads: list[str] = []
    tags: list[str] = []
    for line in output.splitlines():
        # ls-remote prints `<object-id>\t<ref-name>` per line.
        _, _, ref = line.partition("\t")
        if not ref:
            continue
        if ref == "HEAD" or ref.endswith("^{}"):
            continue
        if ref.startswith("refs/heads/"):
            heads.append(ref.removeprefix("refs/heads/"))
        elif ref.startswith("refs/tags/"):
            tags.append(ref.removeprefix("refs/tags/"))
    return heads, tags


def _classify_remote(git: _GitCommand) -> _RemoteState:
    """Classify an existing ``origin`` remote for adoption."""
    heads, tags = _remote_heads_and_tags(git)
    if tags:
        _LOGGER.info(
            "Rejected existing remote adoption because tags are present",
            extra={"reason": "tags_present", "tag_count": len(tags)},
        )
        return _RemoteState(adoptable=False, reason="tags_present")
    if len(heads) > 1:
        _LOGGER.info(
            "Rejected existing remote adoption because multiple heads are present",
            extra={"reason": "multiple_heads", "head_count": len(heads)},
        )
        return _RemoteState(adoptable=False, reason="multiple_heads")
    if not heads:
        _LOGGER.info(
            "Accepted existing remote adoption for a zero-commit remote",
            extra={"reason": "zero_commit_remote"},
        )
        return _RemoteState(adoptable=True, branch=None, reason="zero_commit_remote")

    branch = heads[0]
    git["fetch", "origin", branch]()
    if not _is_empty_initial_commit(git, f"origin/{branch}"):
        _LOGGER.info(
            "Rejected existing remote adoption because the branch has content",
            extra={"reason": "non_empty_history", "branch": branch},
        )
        return _RemoteState(adoptable=False, reason="non_empty_history")
    _LOGGER.info(
        "Accepted existing remote adoption for an empty initial commit",
        extra={"reason": "empty_initial_commit", "branch": branch},
    )
    return _RemoteState(adoptable=True, branch=branch, reason="empty_initial_commit")


def _assert_remote_adoptable(
    *,
    owner: str,
    repo_name: str,
    remote_url: str,
) -> None:
    """Reject a non-empty existing remote before any local scaffolding."""
    git = local["git"]
    with tempfile.TemporaryDirectory() as tmp, local.cwd(tmp):
        git["init"]()
        git["remote", "add", "origin", remote_url]()
        state = _classify_remote(git)
        if not state.adoptable:
            _LOGGER.info(
                "Existing remote adoption rejected",
                extra={
                    "owner": owner,
                    "repo_name": repo_name,
                    "reason": state.reason,
                },
            )
            _die_existing_not_empty(owner=owner, repo_name=repo_name)
        _LOGGER.info(
            "Existing remote adoption accepted",
            extra={
                "owner": owner,
                "repo_name": repo_name,
                "reason": state.reason,
            },
        )


def _is_empty_initial_commit(git: _GitCommand, ref: str) -> bool:
    """Return whether ``ref`` is exactly one empty root commit."""
    if int(git["rev-list", "--count", ref]().strip()) != 1:
        return False

    try:
        git["diff-tree", "--quiet", "--exit-code", "--root", ref]()
    except ProcessExecutionError:
        return False
    return True


def _die_existing_not_empty(*, owner: str, repo_name: str) -> typ.NoReturn:
    """Exit with a conflict when the existing remote contains content."""
    helpers._die_conflict(
        _GIT_FAFO_PREFIX,
        f"GitHub repository '{owner}/{repo_name}' already exists and is not "
        "empty. Pick a new name or delete the existing repository before "
        "running git-fafo again.",
        1,
    )
