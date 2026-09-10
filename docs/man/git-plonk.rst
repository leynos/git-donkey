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
An issue branch such as ``issue-123-short-title`` matches ``(#123)``.
A roadmap branch such as ``road-1-2-3a-4-short-title`` matches
``(road.1.2.3a.4)`` or ``(road.1.2.3a.4.)``.
Leave branches with unrecognized names or missing history markers alone.

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
    Apply the same completion-marker check as default mode.
    Never delete remote branches.

--dry-run
    Print planned actions without removing generated paths, worktrees, or
    branches.
    Combine with default, soft, or hard mode to preview its effects.

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

SEE ALSO
========

**git**\ (1), **git-worktree**\ (1), **git-donkey**\ (1)
