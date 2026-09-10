# git-donkey 0.2.0 migration guide

This guide covers the base-selection and base-update changes in the
forthcoming git-donkey 0.2.0 release, for users upgrading from 0.1.0. The
[default-base and pull-mode design](default-base-and-pull-modes.md) records the
full contract; this guide focuses on the user-visible differences and the
commands that replace the old defaults.

## Who is affected

The changes apply to `git donkey` only. `git track`, `git fafo`, `git plonk`,
and `git donkey-template` are unchanged.

- Repositories whose default branch is not `main`, or whose principal remote
  is not `origin`: an omitted base now follows the remote default instead of
  local `main`.
- Anyone who relied on an omitted base including unpublished local commits
  from `main`: the implicit base is now the fetched remote commit.
- Anyone who relied on the implicit `git pull --rebase` of a behind base:
  base updates are now opt-in.
- Anyone whose local base was not checked out in a worktree and relied on the
  primary checkout being updated: the command now fails instead.
- Scripts that pass an explicit base or `.`: unaffected. Scripts that pass
  `--no-pull` with an omitted base keep suppressing updates, but the base now
  resolves to the remote default instead of local `main`.
- Anyone who relied on a new branch inheriting tracking from its base: new
  branches are now created with `--no-track`.

## Behaviour changes

| Area | 0.1.0 | 0.2.0 |
| --- | --- | --- |
| Omitted base | Local `main`, created from the remote when absent | Principal remote's advertised default branch, fetched and resolved to a commit |
| Base update | Prompted and rebased by default | No update unless `--pull-rebase` or `--pull-ff` is passed |
| Base not in a worktree | Rebases the primary checkout onto the remote base | Fails with exit code 1 |
| Remote default unavailable | Not applicable; the base was local `main` | Fails with exit code 1 and asks for an explicit base |
| Explicit base or `.` | Selected as supplied | Unchanged |
| `--no-pull` | Suppressed the update prompt | Accepted; selects the same no-update default |
| New branch tracking | Could inherit from the base via `branch.autoSetupMerge` | Never inherits (`--no-track`) |

_Table 1: Behaviour changes between git-donkey 0.1.0 and 0.2.0._

### Omitted base selection

In 0.1.0 an omitted base meant local `main`, created from `<remote>/main`
when it was absent. In 0.2.0 it means the default branch advertised by the
principal remote (the first configured remote, as before):

- The command reads the remote's advertised symbolic `HEAD` with
  `git ls-remote --symref <remote> HEAD`. The default branch need not be
  named `main`, and the remote need not be named `origin`.
- The advertised branch is fetched explicitly into
  `refs/remotes/<remote>/<branch>` using
  `+refs/heads/<branch>:refs/remotes/<remote>/<branch>`. This works when the
  configured fetch refspec is narrow, and does not trust a stale
  `refs/remotes/<remote>/HEAD` alias.
- The fetched ref is resolved to a commit, and the worktree is created from
  that commit with `--no-track`, so the new branch does not inherit tracking
  from the remote default branch.

Unpublished local commits and staged, unstaged, or untracked changes in the
primary checkout are not used as the implicit base.

### Base updates are opt-in

Fetching remote references still occurs, but 0.2.0 does not pull or rebase a
local base unless `--pull-rebase` or `--pull-ff` is passed. `--no-pull`
remains supported and explicitly selects the same no-update default. The
three options are mutually exclusive; combining any two exits with code 2
before repository discovery or filesystem changes.

- `--pull-rebase` enables a confirmation prompt and runs
  `git pull --rebase` when the local base is behind its remote counterpart.
- `--pull-ff` enables the same prompt but runs
  `git pull --no-rebase --ff-only`, so divergent histories fail instead of
  being merged or rebased, regardless of configured pull preferences.

### Update scope and failure

An approved update runs only in the worktree recorded as holding the selected
local base. If the base is behind and no worktree holds it, the command fails
with exit code 1 instead of pulling into an unrelated branch in the primary
checkout. The error suggests checking out the base explicitly or omitting the
pull option.

The prompt defaults to no. Declining it, or running without an interactive
terminal, skips the update and creates the worktree from the unchanged base.
If the base is not behind, nothing happens and no prompt is shown. A
local-only base with no remote counterpart keeps the existing skip
behaviour.

With an omitted base, an opted-in update may update the local default branch,
but the new feature branch still starts at the fetched remote commit. Supply
the local base explicitly, or use `.`, to include local commits after an
approved update.

### Unavailable remote defaults

If the default branch cannot be discovered on the principal remote, the
remote advertises no default branch, or the advertised branch cannot be
fetched, the command exits with code 1. When no default is advertised, the
error asks for an explicit base. There is no silent fallback to local `main`,
the primary checkout's branch, or other local work.

A base that exists neither locally nor on the remote also fails with exit
code 1.

### Explicit bases and `.`

Explicit bases and `.` select the same branches as before. A named base picks
that branch, materializing a local tracking branch when the base exists only
on the remote. `.` picks the branch checked out in the calling working
directory, including when called from a linked worktree. If the requested
branch already exists locally or on the remote, it is reused with its
existing tracking rules; the base is used only when creating a new branch.

## Command migration

The examples below show the 0.1.0 command and the 0.2.0 equivalent.

Before (0.1.0):

```shell
# Branch from local main; prompt to pull --rebase when main is behind
git donkey feature/foo

# Branch from local main without the update prompt
git donkey feature/foo --no-pull

# Branch from an explicit local base; prompt to update it
git donkey feature/foo release/1.2
```

After (0.2.0):

```shell
# Branch from the fetched remote default; no pull, rebase, or prompt
git donkey feature/foo

# Spell out the no-update default explicitly
git donkey feature/foo --no-pull

# Use local main, as 0.1.0 did, and opt in to the update prompt
git donkey feature/foo main --pull-rebase

# Select the calling worktree's branch and opt in to the update prompt
git donkey feature/foo . --pull-rebase

# Use an explicit base with a fast-forward-only update prompt
git donkey feature/foo release/1.2 --pull-ff
```

When an explicit local base is behind and is not checked out in any worktree,
enabling a pull mode makes the command exit with code 1. Omitting the pull
option still creates the worktree from the unchanged local base.

## Further reading

- [Users' guide](users-guide.md) documents the current command surface.
- [Default-base and pull-mode design](default-base-and-pull-modes.md) records
  the discovery, preservation, and verification contracts.
