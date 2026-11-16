# Worktree layouts

`git donkey` creates worktrees and `git plonk` retires them, so both commands
must agree on two directories: the _anchor_, which Git commands run from, and
the _worktrees root_, which holds the checkouts `git donkey` creates. This
document records how each is chosen, and why a bare repository gets a root of
its own.

## Context

Both commands originally required a main worktree. They ran Git from it, and
derived the root as its `<main>.worktrees` sibling. A bare repository has no
main worktree, so both commands refused to run in one.

Bare repositories with linked worktrees are an established arrangement. A
session manager such as Lody keeps one collection per repository:

```plaintext
<container>/
  bare.git/                # the repository; no checkout
  worktrees/<session-id>/  # one linked worktree per session
```

Other conventions place the bare repository at `<container>/.bare` or
`<container>/.git`, with worktrees beside it. In every one of them Git lists
the bare repository first in `git worktree list`, marked `bare`, wherever the
command is invoked from.

## Decision

`git_donkey.layout.resolve_layout()` is the single owner of both directories.
`git donkey` and `git plonk` call it and never derive a path themselves, so the
two commands cannot disagree about where donkey worktrees live.

The anchor is the first entry Git lists: the main worktree, or the bare
repository when no main worktree exists. Git runs `git worktree add`,
`git worktree remove`, and branch commands from a bare directory, so no
checkout is needed to act as a home.

The worktrees root is chosen in this order:

1. The `donkey.worktreesRoot` Git configuration value, when set. A relative
   value is resolved against the anchor, and a leading `~` is expanded by Git.
2. For a repository with a main worktree, the `<main>.worktrees` sibling of
   that worktree. This is unchanged.
3. For a bare repository, the `<name>.worktrees` sibling of the bare directory,
   with a trailing `.git` dropped from the name first.

Table 1 shows the default root each arrangement produces.

| Anchor directory            | Kind          | Default worktrees root        |
| --------------------------- | ------------- | ----------------------------- |
| `~/src/demo`                | Main worktree | `~/src/demo.worktrees`        |
| `~/src/demo.git`            | Bare          | `~/src/demo.worktrees`        |
| `<container>/bare.git`      | Bare          | `<container>/bare.worktrees`  |
| `<container>/.bare`         | Bare          | `<container>/.bare.worktrees` |
| `<container>` (bare `.git`) | Bare          | `<container>.worktrees`       |

_Table 1: Default worktrees root for each repository arrangement._

## Why a bare repository gets a separate root

The root is not the directory a session manager already fills. In a Lody
collection, donkey worktrees go to `<container>/bare.worktrees/<branch>`, and
never to `<container>/worktrees/`.

`git plonk` decides scope by path alone: every worktree Git lists under the
root is in scope, whoever created it. That rule is recorded in the
[plonk cleanup policy](plonk-cleanup-policy.md) and is kept. Sharing a
directory with a session manager would therefore put every session's worktree
in scope. A session branch renamed to an issue-style name would be removed once
its marker reached trunk, and `git plonk --soft` would delete generated
directories from sessions that are still running. A separate root makes those
worktrees unreachable without adding a provenance check.

The root stays beside the bare repository, inside the collection's container. A
host that grants a tool access by container, or that provisions inherited
permissions on it, therefore treats donkey worktrees like the collection's
other checkouts. Setting `donkey.worktreesRoot` to a directory outside the
container gives that up.

## Configuration

`donkey.worktreesRoot` is read with `git config --type=path --get`, so it is
honoured at every configuration scope. Storing it in the repository's own
configuration is the usual choice, because every linked worktree shares that
file:

```shell
git config donkey.worktreesRoot ../donkeys
```

An empty value is refused with exit status 2, because it would make the anchor
itself the root. `git plonk` uses the configured root as its scope, so a root
that contains worktrees created by other tools brings them into a sweep.
Changing the value does not move existing worktrees; those left under the old
root fall out of `git plonk`'s scope.

## Fetch refspec precondition

`git clone --bare` configures no fetch refspec for its remote. A fetch then
updates no `refs/remotes/<remote>/*` ref, so a branch that exists on the remote
looks absent, and `git donkey` would create a fresh branch from the default
where it should have tracked the remote one.

`git donkey` therefore refuses, with exit status 2, a principal remote that has
no `remote.<name>.fetch` value, and prints the command that adds the
conventional refspec. It does not add the refspec itself: changing how a
repository fetches is a decision for its owner, and a session manager may
maintain that configuration. The check applies to every layout, because the
hazard does not depend on the repository being bare. `git plonk` is unaffected,
since it fetches the default branch through an explicit refspec.

## Consequences

- A base branch is rarely checked out in a bare collection, so `--pull-rebase`
  and `--pull-ff` usually end with the existing "not checked out in a worktree"
  error. The implicit base is unaffected, because it is the freshly fetched
  remote-tracking ref.
- `git donkey` still requires the invoking directory to have a branch checked
  out. Inside a bare directory that is the branch its `HEAD` names.
- When `git plonk` runs from a bare directory there is no invoking worktree to
  exclude, so every completed worktree under the root is a candidate.

## Verification contract

Unit tests in `tests/unit/test_layout.py` pin the default root for each
arrangement in Table 1, the layout resolved from a linked worktree and from the
bare directory, and the relative, absolute, home-relative, and empty forms of
`donkey.worktreesRoot`. `tests/unit/test_cli_helpers.py` covers the fetch
refspec precondition.

Behavioural tests build a real bare collection with a session worktree.
`features/git_donkey_bare.feature` asserts where a new worktree is created,
that the session directory is left alone, and that a remote without a refspec
is refused before anything is created. `features/git_plonk_bare.feature` gives
the session worktree a completed branch of its own and asserts that default,
hard, and soft sweeps retire only the worktree under the donkey root.
