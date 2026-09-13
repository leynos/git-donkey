---
name: git-donkey-worktrees
description: >-
  Manage linked Git worktrees with git donkey and git plonk. Use when creating
  an isolated branch checkout, locating or reusing an existing worktree,
  preparing a stacked branch, retiring completed worktrees, or explicitly
  cleaning generated directories across donkey worktrees. Do not activate for
  ordinary editing, committing, or pull-request review in an already selected
  checkout. Includes remote-default bases, opt-in pulls, template overlays,
  and dry-run-first cleanup with checks that protect other agents' work.
compatibility: >-
  Requires Git and git-donkey commands on PATH. Verify installed help supports
  remote-default bases, --pull-ff, --pull-rebase, and git plonk --dry-run, and
  that git plonk help describes skipping worktrees with uncommitted or
  untracked files; older releases force removal instead.
---

# Worktree management with git donkey and git plonk

Use `git donkey` to create isolated checkouts and `git plonk` to retire them.
Preserve existing checkouts, unpublished commits, and other agents' work. Treat
creation, history rewriting, and deletion as separate operations with separate
authorization; creating a worktree does not authorize later cleanup.

## Establish the task and repository

1. Read the repository's `AGENTS.md` and applicable local instructions.
   Establish the requested branch, operation, and owner. Reuse an existing
   assigned worktree rather than create another checkout merely because a task
   resumed.
2. Check the installed interface before relying on these workflows:

   ```bash
   git donkey --help
   git plonk --help
   ```

   Older installations may use different defaults or lack dry-run support. Stop
   and report a mismatch; do not improvise flags or silently install an upgrade.
   The package also supplies `git-donkey(1)` and `git-plonk(1)` manual pages.
3. Inspect the calling checkout and the shared worktree inventory:

   ```bash
   git rev-parse --show-toplevel
   git symbolic-ref --quiet --short HEAD
   git status --short --branch --untracked-files=all
   git worktree list --porcelain
   git remote -v
   ```

   A detached `HEAD` requires an explicit decision about the calling checkout;
   the current donkey workflow expects a checked-out branch even when the base
   argument is omitted. Do not switch a shared checkout to make it proceed.
4. Identify the main worktree and the principal remote. Donkey and plonk both
   select the first configured remote, not necessarily `origin`, and both
   discover the default branch it advertises rather than assuming `main`. Do
   not change remote configuration to make an assumption fit. Donkey reports
   `Using remote: ...`; verify that this agrees with the intended remote.
5. Coordinate with other agents before changing shared branch or worktree state.
   Worktrees have separate working files but share repository refs and other Git
   metadata. A new checkout is not a separate clone or a permission boundary.

In the examples below, assign shell variables to inspected values before use.
`main_worktree` and `worktree` mean absolute checkout paths, not branch names.
Use porcelain output as structured data; do not split worktree paths on spaces.
For programmatic parsing, prefer `git worktree list --porcelain -z` and a
NUL-aware parser rather than a shell loop.

## Create an independent worktree

Follow existing issue or roadmap naming conventions when the task has a real
identifier. Do not invent an issue number to enable cleanup. Ordinary feature
names are valid, but default and hard plonk modes do not recognize every name.

Validate a task-specific branch name, then let donkey select the remote default:

```bash
branch='feature/worktree-task'
git check-ref-format --branch "$branch"
git donkey "$branch"
```

Run the steps individually and stop on failure. For a **new** branch, the
omitted base means the default branch currently advertised by the principal
remote, fetched into a remote-tracking ref. It does not mean the primary
checkout's branch, local `main`, a stale remote `HEAD` alias, or the invoking
feature branch. A missing remote default is an error, not permission to
substitute local `main`.

The normal workflow fetches but does not pull, rebase, or prompt to update a
base checkout. `--no-pull` explicitly selects that same behaviour; it is not an
offline mode, and an explicit local base still does not bypass the initial
fetch.

New branches do not automatically track the remote default branch. Publishing
and setting a feature branch's upstream are separate, authorized actions; do not
point its upstream at trunk merely to silence a missing-upstream error.

### Choose a different base deliberately

Use a named base branch to include its committed local state without pulling:

```bash
git donkey feature/release-fix release/1.2
```

To stack new work on the invoking worktree's branch, run these commands **from
the intended parent worktree**. Record its branch and tip commit before
creation:

```bash
git branch --show-current
git rev-parse HEAD
git donkey feature/child .
```

`.` selects the branch checked out in the calling directory, including a linked
worktree. It includes that branch's commits, not its uncommitted or untracked
files. Keep the recorded parent tip with the task's handover notes.

A supplied base affects **new branches only**. It does not reset or rebase an
existing feature branch. Do not treat the positional base as a general-purpose
commit-ish interface; the documented explicit forms are branch names and `.`.

### Update a base only when requested

These are alternative, explicit operations, not part of normal creation:

```bash
git donkey feature/ff-task release/1.2 --pull-ff
```

```bash
git donkey feature/rebase-task release/1.2 --pull-rebase
```

`--pull-ff` offers a fast-forward-only pull; divergence fails without merging or
rebasing. `--pull-rebase` offers a pull with rebase. Both retain a confirmation
prompt when the local base is behind. A declined prompt or non-interactive
terminal skips the update; the flag alone does not prove an update happened.
Never pipe an automatic confirmation into this workflow.

The options `--pull-ff`, `--pull-rebase`, and `--no-pull` are mutually
exclusive. An approved update runs in the worktree holding the selected local
base. An unavailable base checkout is not permission to update another branch.
With an omitted base, creation still uses the remote commit even after an
approved local-base update; name the local base or use `.` to include its local
commits.

## Reuse, locate, and verify

Donkey prefers an existing local branch, then a same-named branch on the
principal remote, and otherwise creates a new branch. A remote-only branch gets
a local tracking branch. An existing local branch keeps its upstream, or needs a
same-named remote branch from which donkey can set one. A local-only branch
without an upstream can therefore fail; do not invent an upstream or push it
without authorization.

If Git already lists the requested branch in a worktree, inspect that path and
its owner. Reuse it only when assigned or explicitly handed over. Donkey treats
an occupied branch or an existing target path as a conflict, not an idempotent
success. Do not delete the path, reset the branch, prune metadata, or force a
second checkout to bypass the conflict.

The layout is a sibling of the **main worktree**:
`<main-parent>/<main-name>.worktrees/<branch>`. Branch slashes create nested
path components. It is not necessarily relative to the shell's current
directory. After creation, read the inventory again and select the path for the
exact `refs/heads/<branch>` entry:

```bash
git worktree list --porcelain
git -C "$worktree" branch --show-current
git -C "$worktree" rev-parse HEAD
git -C "$worktree" status --short --branch --untracked-files=all
git -C "$worktree" diff --no-ext-diff --stat
```

Confirm the expected branch and, for a new branch, the selected starting commit.
Recheck any pre-existing checkout whose preservation matters. Do not assume a
successful command changed the parent shell's directory; use `git -C` or enter
the verified path explicitly before editing. Read the new checkout's applicable
instructions before starting work.

### Account for template overlays and partial creation

Donkey automatically copies repository template overlays into new worktrees. An
overlay can overwrite tracked files after issuing a warning, so a newly created
worktree need not be clean. Inspect its status and diff before editing or
committing; never sweep unrelated template changes into the task's commit. Do
not dump local configuration or credentials into logs.

`git donkey-template` displays **and creates** the template directory. It is not
a read-only inspection command and is unnecessary for ordinary worktree
creation. Do not change templates as a side effect of managing a worktree.

An overlay failure can leave a successfully created worktree behind even when
donkey reports failure. Inspect the inventory and partial changes before any
retry. Do not run cleanup to erase evidence or blindly repeat creation.

## Maintain a stack without guessing history

After a parent branch merges, especially by squash merge, neither donkey nor
plonk determines the correct child-branch rebase boundary. Keep the recorded
parent tip and branch relationship. Hand history rewriting to the repository's
rebase workflow, with verified old and new boundaries and its own safety checks.
Do not derive the old boundary from a post-squash merge-base alone, and do not
retire a parent worktree until its dependent work has been accounted for.

## Clean up only after a reviewed preview

**Read [the cleanup checklist](references/cleanup.md) before every cleanup,
including `--soft`.** It documents safeguards the command does not enforce.
Plonk skips a completed worktree holding modified, staged, or untracked files
and reports it, but it deletes ignored files with the worktree, and `--hard`
deletes a removed worktree's local branch with `git branch -D`, without a
merged check. Completion markers do not prove that current work is safe to
discard.

Default and hard runs, previews included, contact the principal remote: plonk
discovers and fetches its advertised default branch before every sweep, and
fails without removing anything when the remote is unreachable or advertises
no default branch. Start with the preview for the requested mode from a
deliberately selected invoking worktree, normally the main worktree. Keep that
location unchanged between preview and execution:

```bash
git -C "$main_worktree" plonk --dry-run
```

Use `--soft --dry-run` for generated directories or `--hard --dry-run` only when
branch deletion is in scope. There is no documented per-path or per-branch
selector. If the preview reaches beyond the authorized scope, do not execute
that plonk mode. A no-op result is not a reason to escalate to `--hard` or
`--soft`.

## Report the result

For creation or reuse, report the branch, absolute worktree path, chosen base,
starting commit, whether any pull occurred, and unexpected overlay changes. For
cleanup, report the mode, inspected trunk ref, reviewed targets, actual
removals, retained branches, each skipped worktree with its reported reason,
any failed branch deletion, and the exit status. Skips exit `0`; a refused
branch deletion exits `1` with its worktree already removed. Distinguish a
preview, a completed operation, and a partial failure. Do not imply a push,
merge, rebase, or remote-branch deletion took place when none was requested or
performed.
