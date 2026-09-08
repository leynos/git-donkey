git-donkey
==========
Create linked Git worktrees
--------------------------

:Manual section: 1
:Manual group: Git-donkey manual

SYNOPSIS
========

**git donkey** *BRANCH_NAME* [*ORIGIN_BRANCH*] [**--no-pull**]

**git-donkey** *BRANCH_NAME* [*ORIGIN_BRANCH*] [**--no-pull**]

DESCRIPTION
===========

Create a linked worktree at ``../{repo}.worktrees/{branch}``.
Use ``main`` as the default base, an explicitly named base branch, or ``.``
for the branch checked out in the current working directory.
Prefer the ``origin`` remote and fall back to the first remote when needed.
Reuse an existing local or remote branch rather than create it again.

When the base is behind its remote counterpart, prompt before running
``git pull --rebase`` unless **--no-pull** was supplied.
Skip that check when the base has no remote counterpart.
An existing worktree path or a branch checked out elsewhere is a conflict.

After creating the worktree, copy the repository's template overlay into it.
Existing destination files receive a warning and are overwritten.
See **git-donkey-template**\ (1) for template directory discovery.

OPTIONS
=======

BRANCH_NAME
    Branch to check out in the new worktree.

ORIGIN_BRANCH
    Base branch for a new branch; default ``main``.
    The value ``.`` selects the current branch.

--no-pull
    Do not prompt to pull the base branch when it is behind the remote.

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

Create a worktree from ``main``::

    git donkey feature/example

Use the current branch as the base::

    git donkey feature/example .

Use a release branch without prompting for a pull::

    git donkey feature/example release/1.2 --no-pull

SEE ALSO
========

**git**\ (1), **git-worktree**\ (1), **git-track**\ (1),
**git-plonk**\ (1), **git-donkey-template**\ (1)
