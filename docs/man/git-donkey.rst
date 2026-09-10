git-donkey
==========
Create linked Git worktrees
---------------------------

:Manual section: 1
:Manual group: Git-donkey manual

SYNOPSIS
========

**git donkey** *BRANCH_NAME* [*ORIGIN_BRANCH*]
[**--pull-rebase** | **--pull-ff** | **--no-pull**]

**git-donkey** *BRANCH_NAME* [*ORIGIN_BRANCH*]
[**--pull-rebase** | **--pull-ff** | **--no-pull**]

DESCRIPTION
===========

Create a linked worktree at ``../{repo}.worktrees/{branch}``.
Use the default branch advertised by the first configured remote as the base,
an explicitly named base branch, or ``.`` for the branch checked out in the
calling working directory, including when called from a linked worktree.
The default branch need not be named ``main``, and the remote need not be
named ``origin``.
Reuse an existing local or remote branch rather than create it again.

The command fetches remote references but does not pull, rebase, or prompt by
default. Unpublished local commits and uncommitted changes in the primary
checkout are not used as the implicit base. When no remote advertises a
default branch, the command fails and asks for an explicit base rather than
falling back to a local ``main``.

An existing worktree path or a branch checked out elsewhere is a conflict.

After creating the worktree, copy the repository's template overlay into it.
Existing destination files receive a warning and are overwritten.
See **git-donkey-template**\ (1) for template directory discovery.

OPTIONS
=======

BRANCH_NAME
    Branch to check out in the new worktree.

ORIGIN_BRANCH
    Base branch for a new branch; defaults to the first remote's default
    branch. The value ``.`` selects the branch checked out in the calling
    directory.

--pull-rebase
    Prompt to run ``git pull --rebase`` when the local base is behind its
    remote counterpart.

--pull-ff
    Prompt to update the local base with ``git pull --no-rebase --ff-only``
    when it is behind. Divergent histories fail rather than being merged or
    rebased, regardless of configured pull preferences.

--no-pull
    Do not update the local base. This is the default and remains supported
    for compatibility; remote references are still fetched.

These three options are mutually exclusive. A declined prompt, or a
non-interactive terminal, skips the update. An approved update runs only in
the worktree holding the selected local base; when that branch is not checked
out, the command fails rather than updating an unrelated primary checkout.
With an omitted base, the new branch still starts at the fetched remote
commit even when an approved update moves the local default branch. Supply
the local base explicitly, or use ``.``, to include local commits.

-h, --help
    Display command-line help and exit.

--version
    Display the installed version and exit.

FILES
=====

On Linux, template overlays live under
``${XDG_DATA_HOME:-$HOME/.local/share}/git-donkey/template/<repo-url-slug>``.
Other platforms use their platform-specific user data directory.

EXAMPLES
========

Create a worktree from the remote's default branch::

    git donkey feature/example

Use the current branch as the base::

    git donkey feature/example .

Update a local base by rebase before creating the worktree::

    git donkey feature/example release/1.2 --pull-rebase

Allow only a fast-forward update of the base::

    git donkey feature/example release/1.2 --pull-ff

SEE ALSO
========

**git**\ (1), **git-worktree**\ (1), **git-track**\ (1),
**git-plonk**\ (1), **git-donkey-template**\ (1)
