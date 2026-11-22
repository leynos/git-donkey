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

Create a linked worktree at ``../{repo}.worktrees/{branch}``, a sibling of the
main worktree.
In a bare repository, whose checkouts are all linked worktrees, the worktree is
created beside the bare directory instead, at ``{name}.worktrees/{branch}``
with a trailing ``.git`` dropped from the directory name.
The ``donkey.worktreesRoot`` Git configuration value replaces either
directory; a relative value is resolved against the main worktree or the bare
repository.
Use the default branch advertised by the first configured remote as the base,
or supply an explicit base: a branch name, ``.`` for the branch checked out in
the calling working directory including when called from a linked worktree, a
remote-qualified branch such as ``origin/main``, a tag, or any revision Git
resolves to a commit.
The default branch need not be named ``main``, and the remote need not be
named ``origin``.
The command fetches the first configured remote before resolving an explicit
base; a ref belonging to another configured remote is resolved as that fetch
left it.
Reuse an existing local or remote branch rather than create it again.

An explicit base is interpreted in this order: a fully qualified ``refs/``
value is used as written; otherwise a literal local branch name keeps its own
meaning; otherwise a configured ``<remote>/<branch>`` whose remote-tracking
ref exists is used; otherwise Git resolves the value to a commit.

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
    Base for the new branch; defaults to the first remote's default branch.
    Accepts a local branch name, ``.`` for the calling branch, a
    remote-qualified branch such as ``origin/main``, a tag, or any revision
    Git resolves to a commit. A local branch name takes precedence over the
    remote-tracking ref it might also spell.

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
A remote-qualified base such as ``origin/main`` updates the local branch of
the same name, as an omitted base does, while the new branch still starts at
the remote-tracking commit. A base that names a tag or commit has no branch
to update, so it neither prompts nor updates.

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

Start from the fetched remote tip even when local ``main`` is stale::

    git donkey feature/example origin/main

Start from a tag or a commit::

    git donkey feature/example v1.2.0
    git donkey feature/example 4f3c2a1

Update a local base by rebase before creating the worktree::

    git donkey feature/example release/1.2 --pull-rebase

Allow only a fast-forward update of the base::

    git donkey feature/example release/1.2 --pull-ff

SEE ALSO
========

**git**\ (1), **git-worktree**\ (1), **git-track**\ (1),
**git-plonk**\ (1), **git-donkey-template**\ (1)
