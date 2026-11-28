git-plonk
=========
Clean up git-donkey worktrees
-----------------------------

:Manual section: 1 :Manual group: Git-donkey manual

SYNOPSIS
========

**git plonk** [**--soft** | **--hard**] [**--dry-run**] [**--no-github**]
[**--upstream-gone**]

**git-plonk** [**--soft** | **--hard**] [**--dry-run**] [**--no-github**]
[**--upstream-gone**]

DESCRIPTION
===========

Clean up linked worktrees listed by Git under the worktree root **git-donkey**
uses: the main repository's ``../{repo}.worktrees`` directory, the
``{name}.worktrees`` sibling of a bare repository, or the directory named by the
``donkey.worktreesRoot`` Git configuration value. A bare repository's trailing
``.git`` is dropped before ``.worktrees`` is added, so ``/container/bare.git``
is scanned as ``/container/bare.worktrees``. Run the command inside a Git
repository, including a linked topic worktree or a bare repository directory.
Worktrees outside that directory remain untouched.

Default mode removes worktrees whose completion a tier resolves and which Git
would discard unprompted. Completion is resolved in three tiers.

Offline branch names are recognized first. An issue branch such as
``issue-123-short-title`` completes on ``(#123)`` in canonical trunk history. A
df12-build roadmap branch such as ``roadmap-2-2-1`` or
``roadmap-2-2-1-addendum`` completes on its un-namespaced dotted id
``(2.2.1)``; the id may have two to four numeric components. A legacy roadmap
branch such as ``road-1-2-3a-4-short-title`` completes on ``(road.1.2.3a.4)``,
and a task id may end the name, so ``road-1-2-3a-4`` also works.

An issue branch additionally accepts the signals GitHub writes into commit
messages: a closing keyword such as ``Closes #123``, with ``Fixes`` and
``Resolves`` and their inflections, a required separator (whitespace, or a
colon with optional surrounding whitespace), and no owner or repository prefix;
a classic merge commit
``Merge pull request #45 from owner/issue-123-short-title`` in the subject of a
real merge commit, whose branch segment matches exactly, whatever its
pull-request number; and the branch name appearing verbatim in a bounded
context.

When no offline signal resolves a branch, the GitHub merged-pull-request
fallback asks whether a merged pull request exists for it. The fallback is
enabled by default when the principal remote is GitHub and ``gh`` is installed.
The query is read-only, and a result is accepted only when its head branch,
base branch (the advertised trunk), head owner, and head commit match the
request. The commit check keeps a reused branch name from completing new work
on the strength of an earlier merge. A failed, timed-out, malformed, or
mismatched response counts as unknown, never merged. A missing ``gh`` is
reported once as a notice and disables the fallback. Pass **--no-github** to
disable it explicitly.

The opt-in **--upstream-gone** tier treats a branch whose configured upstream
no longer exists as completed. It reads ``branch.<name>.remote`` and
``branch.<name>.merge`` and asks
``git ls-remote --exit-code --heads <remote> refs/heads/<branch>``. Only a
deleted branch completes a candidate: an upstream that still exists, was never
configured, or cannot be queried leaves it unverified. The signal is opt-in
because a deleted branch cannot distinguish a merged change from an abandoned
one.

A candidate no tier resolves is listed under ``Unverified worktrees:`` with a
fixed reason and is never removed. Unverified worktrees, like skips, leave the
exit status at ``0``.

The canonical trunk is the default branch advertised by the principal remote
(the first configured remote) and fetched before the sweep, so the local
``refs/remotes/<remote>/HEAD`` alias is never consulted and there is no
fallback to local ``main``.

A completed worktree holding modified, staged, or untracked files is skipped
and reported, and the sweep continues with the remaining worktrees, so one
dirty candidate cannot leave clean siblings behind. Files ignored by
``.gitignore`` do not protect a worktree, matching the rule
``git worktree remove`` itself applies. A worktree with a locked registration
is skipped as ``worktree is locked``, including in a dry run, because Git
refuses to remove it. Nothing in this command forces a removal, so a skipped
worktree stays on disk with its branch intact. The summary annotates each
removal with the signal that justified it, lists every skipped worktree with
the reason it was skipped, and lists every unverified worktree with its fixed
reason. A branch Git refuses to delete is reported in a
``Failed branch deletions:`` section. The sweep continues with the remaining
candidates, and the command exits with status ``1``.

The command exits with one of the following statuses:

``0``
    The sweep did everything it planned, including any reported skips and
    unverified worktrees.

``1``
    The trunk could not be resolved, or a local branch could not be deleted.

``2``
    The command could not run, for example ``--soft`` combined with
    ``--hard``, ``--no-github``, or ``--upstream-gone``.

OPTIONS
=======

--soft
    Remove generated directories from all git-donkey worktrees without
    removing worktrees or branches.
    Generated directory names include ``target``, ``node_modules``, ``.venv``,
    ``.tox``, ``.mypy_cache``, ``.pytest_cache``, ``.ruff_cache``, ``htmlcov``,
    ``dist``, ``build``, and ``coverage``.
    Soft cleanup does not judge completion, so it cannot be combined with
    ``--no-github`` or ``--upstream-gone``.

--hard
    Remove completed worktrees and delete their matching local branches.
    Apply the same completion tiers and the same cleanliness check as default
    mode.
    A worktree skipped for uncommitted or untracked files keeps its branch,
    because the branch is deleted only after its worktree is removed; a
    refused branch deletion is reported rather than fatal.
    The branch is force-deleted, but only while it still points at the commit
    the sweep recorded and no other worktree still checks it out, so a commit
    made during the sweep keeps the branch, and the deleted branch's tracking
    configuration is removed.
    A merged pull request matched by name does not prove that every local
    commit on the branch was merged, so inspect each branch tip before running
    it.
    Never force a removal and never delete remote branches.

--dry-run
    Print planned actions without removing generated paths, worktrees, or
    branches.
    Combine with default, soft, or hard mode to preview its effects.
    Worktrees that would be skipped or left unverified are reported in the
    preview as well.

--no-github
    Disable the GitHub merged-pull-request fallback, even when the principal
    remote is GitHub and ``gh`` is installed.
    Offline signals and the opt-in upstream check are unaffected.
    Not valid with ``--soft``.

--upstream-gone
    Treat a branch whose configured upstream no longer exists as completed.
    The check is opt-in because a deleted branch cannot distinguish a merged
    change from an abandoned one; an upstream that still exists, was never
    configured, or cannot be queried leaves the candidate unverified.
    A remote that hides refs with ``uploadpack.hideRefs`` can make an existing
    upstream look deleted, so this check cannot prove absence there.
    Not valid with ``--soft``.

-h, --help
    Display command-line help and exit.

--version
    Display the installed version and exit.

EXAMPLES
========

Preview removal of completed worktrees::

    git plonk --dry-run

Preview removal of generated directories::

    git plonk --soft --dry-run

Preview removal of completed worktrees and local branches::

    git plonk --hard --dry-run

Remove completed worktrees after reviewing the preview::

    git plonk

Disable the GitHub merged-pull-request lookup (the advertised trunk is still
fetched)::

    git plonk --no-github

Treat a deleted upstream branch as completion evidence::

    git plonk --upstream-gone --dry-run

A run that leaves work behind names each skipped or unverified worktree and the
reason, and each removal names its completion signal::

    git-plonk: mode=hard
    Removed worktrees:
    - /home/user/demo.worktrees/issue-123-fix (completed by marker (#123))
    - /home/user/demo.worktrees/fix-markdown-gates (completed by merged pull request #61)
    Removed branches:
    - issue-123-fix
    - fix-markdown-gates
    Skipped worktrees:
    - /home/user/demo.worktrees/issue-456-dirty (uncommitted changes)
    - /home/user/demo.worktrees/issue-789-gone (worktree directory is missing)
    Unverified worktrees:
    - /home/user/demo.worktrees/adopt-whitaker (no completion signal found)

SEE ALSO
========

**git**\ (1), **git-worktree**\ (1), **git-donkey**\ (1)
