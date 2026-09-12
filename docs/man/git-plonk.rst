git-plonk
=========
Clean up git-donkey worktrees
-----------------------------

:Manual section: 1
:Manual group: Git-donkey manual

SYNOPSIS
========

**git plonk** [**--soft** | **--hard**] [**--dry-run**]

**git-plonk** [**--soft** | **--hard**] [**--dry-run**]

DESCRIPTION
===========

Clean up linked worktrees listed by Git under the main repository's
``../{repo}.worktrees`` directory.
Run the command inside a Git repository, including a linked topic worktree.
Worktrees outside that directory remain untouched.

Default mode removes completed worktrees.
A worktree counts as completed only when its branch name yields a recognized
completion marker that appears in the canonical trunk history.
The canonical trunk is the default branch advertised by the principal remote
(the first configured remote) and fetched before the sweep, so the local
``refs/remotes/<remote>/HEAD`` alias is never consulted and there is no
fallback to local ``main``.
An issue branch such as ``issue-123-short-title`` matches ``(#123)``.
A roadmap branch such as ``road-1-2-3a-4-short-title`` matches
``(road.1.2.3a.4)`` or ``(road.1.2.3a.4.)``.
Leave branches with unrecognized names or missing history markers alone.

A completed worktree holding modified, staged, or untracked files is skipped
and reported, and the sweep continues with the remaining worktrees, so one
dirty candidate cannot leave clean siblings behind.
Files ignored by ``.gitignore`` do not protect a worktree, matching the rule
``git worktree remove`` itself applies.
Nothing in this command forces a removal, so a skipped worktree stays on disk
with its branch intact.
The summary lists removed worktrees, deleted branches, and every skipped
worktree with the reason it was skipped.
A branch Git refuses to delete is reported in a ``Failed branch deletions:``
section.
The sweep continues with the remaining candidates, and the command exits with
status ``1``.

The command exits with one of the following statuses:

``0``
    The sweep did everything it planned, including any reported skips.

``1``
    The trunk could not be resolved, or a local branch could not be deleted.

``2``
    The command could not run, for example ``--soft`` combined with
    ``--hard``.

OPTIONS
=======

--soft
    Remove generated directories from all git-donkey worktrees without
    removing worktrees or branches.
    Generated directory names include ``target``, ``node_modules``, ``.venv``,
    ``.tox``, ``.mypy_cache``, ``.pytest_cache``, ``.ruff_cache``, ``htmlcov``,
    ``dist``, ``build``, and ``coverage``.

--hard
    Remove completed worktrees and delete their matching local branches.
    Apply the same completion-marker check and the same cleanliness check as
    default mode.
    A worktree skipped for uncommitted or untracked files keeps its branch,
    because the branch is deleted only after its worktree is removed; a
    refused branch deletion is reported rather than fatal.
    Never force a removal and never delete remote branches.

--dry-run
    Print planned actions without removing generated paths, worktrees, or
    branches.
    Combine with default, soft, or hard mode to preview its effects.
    Worktrees that would be skipped are reported in the preview as well.

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

A run that leaves work behind names each skipped worktree and the reason::

    git-plonk: mode=default
    Removed worktrees:
    - /home/user/demo.worktrees/issue-123-fix
    Skipped worktrees:
    - /home/user/demo.worktrees/issue-456-dirty (uncommitted changes)
    - /home/user/demo.worktrees/issue-789-gone (worktree directory is missing)

SEE ALSO
========

**git**\ (1), **git-worktree**\ (1), **git-donkey**\ (1)
