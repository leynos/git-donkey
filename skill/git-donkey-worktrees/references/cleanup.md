# Git plonk cleanup checklist

Read this reference before executing any cleanup mode. Preview is necessary but
not sufficient: plonk reports candidate actions, not a proof that data is safe
to delete. No mode provides an interactive confirmation gate.

## Understand the scope and modes

Plonk must run inside a Git repository. It considers Git-registered linked
worktrees under the worktree root donkey uses: the `<main-name>.worktrees`
sibling of the main worktree, the `<bare-name>.worktrees` sibling of a bare
repository, or the directory `donkey.worktreesRoot` names. A bare repository's
trailing `.git` is dropped first, so `/container/bare.git` is scanned as
`/container/bare.worktrees`. In a bare collection, session worktrees kept
elsewhere, such as under `<container>/worktrees/`, are out of scope. This is a
path-based scope, not a provenance check: a manually created worktree under
that directory can also qualify. It does not sweep arbitrary unregistered
directories.

- Default mode removes clean worktrees whose completion a tier resolves. It
  keeps local branches. A branch no tier resolves is reported as unverified and
  kept.
- `--hard` uses the same completion tiers and cleanliness check, and also
  deletes the local branch of each worktree it removed. It never deletes remote
  branches and does not broaden completion matching.
- `--soft` removes named generated directories from **all** worktrees in scope,
  irrespective of branch naming or completion. It keeps worktree registrations
  and branches, but does not check whether the directory contains tracked files.
- `--dry-run` previews the selected mode without deleting paths or branches. It
  reports the worktrees a real run would skip or leave unverified, and why.
  `--soft` and `--hard` are mutually exclusive.
- `--no-github` disables the GitHub merged-pull-request fallback. It is not
  valid with `--soft`.
- `--upstream-gone` adds the opt-in check that a branch's configured upstream
  no longer exists. It is not valid with `--soft`.

Default and hard modes exclude the invoking worktree. Soft mode does **not**
exclude it. All modes can affect other agents' worktrees. Execute a reviewed
preview from the same invoking worktree: changing directory can change the
candidate set. Running from the main worktree, or from the bare directory in a
bare collection, avoids excluding a linked worktree merely because the shell
happens to be inside it.

## Know what the cleanliness check protects

Default and hard modes remove a worktree with an unforced `git worktree remove`
after a preflight that mirrors Git's own refusal. A completed worktree holding
modified, staged, or untracked files is skipped, listed under
`Skipped worktrees:` with the reason `uncommitted changes`, and left on disk
with its branch. The sweep continues with the remaining candidates, and skips
leave the exit status at `0`. There is no option that forces a removal; do not
add `--force` to any plonk invocation.

The check protects only what Git itself would refuse to discard:

- Files matched by `.gitignore` do not protect a worktree. Ignored build
  output, installed environments, caches, and local configuration are deleted
  with it.
- Committed but unpushed commits do not protect a worktree. Default mode keeps
  the branch, so those commits survive on it; hard mode does not.
- A registered worktree whose directory is missing is skipped with the reason
  `worktree directory is missing`. Any other refusal during removal is reported
  as `worktree removal failed`, and the worktree stays registered.

Hard mode deletes a branch only after its worktree was removed, using
`git branch -D`. That deletion performs no merged or ancestry check, so a
branch carrying commits after the matching marker is deleted with them. A
branch Git refuses to delete is listed under `Failed branch deletions:`; its
worktree is already gone, the sweep continues, and the run exits with status
`1`.

## Resolve completion history before default or hard cleanup

Plonk resolves trunk the same way donkey selects an implicit base. It takes the
first configured remote as the principal remote, reads the default branch that
remote advertises with `git ls-remote --symref`, and fetches that branch into
`refs/remotes/<remote>/<branch>` before scanning its history. It never consults
the local `refs/remotes/<remote>/HEAD` alias and never falls back to local
`main`. This happens on every default or hard run, `--dry-run` included, so
those modes need access to the principal remote. An unreachable remote, or one
that advertises no default branch, is an error with exit status `1`, and
nothing is removed.

Inspect the configured remotes and the advertised default before previewing:

```bash
git -C "$main_worktree" remote -v
git -C "$main_worktree" ls-remote --symref "$remote" HEAD
```

Assign `remote` from the first configured remote, not a guessed `origin`.
Confirm that the advertised branch is the intended trunk: plonk follows the
remote's advertisement even when the repository integrates elsewhere, and a
repository whose first remote is a fork or mirror judges completion against
that remote. Do not reorder or rewrite remotes to steer the selection. Never
add a nonexistent `git plonk --fetch`, `--remote`, or `--base` option. When the
remote cannot be reached, report that cleanup could not resolve trunk rather
than substitute local history.

The advertised branch is also the base branch the GitHub fallback requires a
merged pull request to target. Before relying on that tier, inspect the raw
configured URL and the CLI session:

```bash
git -C "$main_worktree" config --get "remote.$remote.url"
gh auth status
```

Plonk parses the raw URL without applying `insteadOf`, so a transport rewrite
to a local mirror does not change which GitHub repository is queried. Use
`--no-github` when the sweep must make no GitHub API call; default and hard
runs still discover and fetch the advertised trunk from the principal remote.
Treat a missing or unauthenticated `gh` as disabling the tier rather than as
evidence of completion.

## Treat completion evidence as a claim to verify

Completion is resolved in three tiers, and each removal line names the tier
that justified it. Do not treat any tier as proof that the work is safe to
discard; verify the actual merge and the worktree's contents.

**Offline history.** An issue-style branch such as `issue-123-short-title` maps
to `(#123)`; the matcher also accepts `(#123.)`. A df12-build roadmap branch
such as `roadmap-2-2-1` or `roadmap-2-2-1-addendum` maps to `(2.2.1)`, with two
to four numeric components. A legacy roadmap branch such as
`road-1-2-3a-4-short-title` maps to `(road.1.2.3a.4)`, and a task id may end
the name. An issue branch also accepts a closing keyword such as `Closes #123`,
a classic merge commit naming the exact branch, and the branch name appearing
verbatim in a bounded context. Unrecognized branches such as `feature/plain`
have no offline matcher. Do not rename a branch just to make cleanup recognize
it.

**GitHub fallback.** For a branch no offline signal resolves, plonk asks
whether a merged pull request exists for that head branch when the principal
remote is GitHub and `gh` is installed. It accepts only a result whose head
branch, base branch (the advertised trunk), and head owner match. A failed,
timed-out, malformed, or mismatched response is unknown, never merged. `gh`
being absent is reported once and disables the tier. `--no-github` disables it
explicitly. The fallback is read-only and never merges, closes, or edits
anything.

**Opt-in upstream check.** `--upstream-gone` treats a branch whose configured
upstream no longer exists as completed. The check reads `branch.<name>.remote`
and `branch.<name>.merge` and runs `git ls-remote --exit-code --heads`. Only
`gone` completes a candidate; `unset`, `present`, and `unknown` leave it
unverified. The signal is weak because a deleted branch cannot distinguish a
merged change from an abandoned one. Use it only when the operator accepts that
risk.

A candidate no tier resolves is reported under `Unverified worktrees:` with one
of the fixed reasons `no completion signal found`,
`GitHub completion lookup failed`, `no upstream branch configured`,
`upstream branch still exists`, or `upstream state unknown`. It is never
removed. An unverified result is not a failure: it leaves the exit status at
`0`.

A matching reference anywhere in a trunk commit message is enough for the
offline tier. That is weaker than it looks: it does not prove patch
equivalence, branch ancestry, or that the merged work matches the branch. A
squash-merge suffix often names a pull request, which may have a different
number from the issue in the branch name. Verify that the evidence really
corresponds to the completed work; neither a closed issue nor an unrelated
`(#123)` establishes that relationship.

A branch may contain new commits after an earlier matching merge. Several
worktrees can also share the same issue marker. Review every candidate
individually; do not approve a batch solely because one matching task finished.

## Review every candidate before removing a worktree

1. Establish authorization for the **entire** preview, including each worktree
   and, in hard mode, each branch. Identify its owner, dependent branches,
   running agents, builds, watchers, and shells. Coordinate a pause or
   handover; do not terminate another task merely to make cleanup possible.
2. Record each candidate's absolute path, branch, and tip commit. Inspect
   tracked, untracked, ignored, and submodule state. Do not log secret file
   contents:

   ```bash
   git -C "$worktree" rev-parse HEAD
   git -C "$worktree" status --short --untracked-files=all --ignored
   git -C "$worktree" diff --no-ext-diff --stat
   git -C "$worktree" diff --no-ext-diff --cached --stat
   git -C "$worktree" submodule status --recursive
   ```

   A clean ordinary status does not prove that ignored local data is
   expendable: the cleanliness check skips modified, staged, and untracked
   files, but ignored data is deleted with the worktree. Inspect submodule
   working changes separately when present. Leave unresolved edits or
   in-progress Git operations untouched. Saving a tip commit alone does not
   preserve working files or ignored data.
3. Assign `trunk_ref` to `refs/remotes/<remote>/<branch>` for the advertised
   default inspected above. Inspect commits that trunk does not reach:

   ```bash
   git -C "$worktree" log --oneline "$trunk_ref"..HEAD
   ```

   Check publication to the intended feature upstream separately when relevant.
   Missing upstreams and failed fetches are unknowns, not evidence of no work.
   After a squash merge, ancestry alone cannot distinguish merged patches from
   later changes. Verify the actual merge and patch content, including commits
   added after it. Retain the worktree when uncertain. Default mode's retained
   branch preserves committed history, but not the checkout's uncommitted data.
4. For hard mode, establish that the local branch has no needed unique history,
   recovery role, or unresolved stack dependency. A marker match does not
   authorize `-D`. Retain branches by choosing default mode when branch
   deletion is not necessary or not explicitly in scope.
5. Re-run the same dry-run immediately before execution from the same invoking
   worktree. Confirm that candidate tips, local changes, and active-task
   ownership have not changed. A preview is not a lock or a transaction. If
   another agent can still mutate the candidates, do not run destructive
   cleanup.

## Apply the reviewed mode

Choose **one** of these preview commands; do not run all modes in sequence:

```bash
git -C "$main_worktree" plonk --dry-run
```

```bash
git -C "$main_worktree" plonk --soft --dry-run
```

```bash
git -C "$main_worktree" plonk --hard --dry-run
```

When the sweep must make no GitHub API call, add `--no-github`; the default or
hard run still fetches the advertised trunk. When the operator has accepted the
gone-upstream risk, add `--upstream-gone`:

```bash
git -C "$main_worktree" plonk --no-github --dry-run
```

```bash
git -C "$main_worktree" plonk --upstream-gone --dry-run
```

Either flag changes the candidate set: `--no-github` can move candidates from
removal to unverified, and `--upstream-gone` can add candidates that default
mode would keep. Re-run the preview with the exact flags that will be used for
execution, and review the resulting set again.

Only after the corresponding checks and authorization succeed, execute the same
command with `--dry-run` removed. The command offers no per-target selector. If
any candidate is unsafe or out of scope, stop the batch. Do not invent an
exclude flag, use invocation-directory changes as a filtering trick, or
reinterpret a request for one worktree as permission to clean every worktree.

For a separately authorized single-worktree removal, use Git's non-forced
operation after the same data-preservation checks:

```bash
git -C "$main_worktree" worktree remove "$worktree"
```

This leaves the branch intact. Treat a refusal as a reason to investigate, not
to add `--force`. Do not substitute recursive directory removal, hard resets,
blanket `git clean`, or blind `git worktree prune` for managed removal.

## Additional requirements for soft cleanup

The current top-level target names are `target`, `node_modules`, `.venv`,
`.tox`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `htmlcov`, `dist`,
`build`, and `coverage`. Plonk removes existing directories recursively. For a
target that is a symlink, it unlinks the symlink rather than deleting its
destination.

`--soft` is not a safe spelling of default cleanup. It reaches unfinished and
invoking worktrees, does not require trunk history, and can invalidate active
builds, installed environments, caches, or tracked content under these names.

For each planned generated path, inspect its contents and check whether Git
tracks anything there. Set `generated_relative_path` relative to that worktree:

```bash
git -C "$worktree" ls-files -- "$generated_relative_path"
```

A familiar directory name does not prove its contents are reproducible. Obtain
permission to discard the exact data and account for rebuild cost. Coordinate
with running tasks in **every** affected worktree before execution. Do not use
soft mode as an unsolicited disk-pressure or post-task housekeeping action.

## Verify and report

Re-read `git worktree list --porcelain`, check retained branch refs and paths,
and inspect surviving checkouts after execution. Compare actual results with
the reviewed preview, including each skipped worktree and its reason and each
unverified worktree and its fixed reason. Check that every removal line named
the completion signal the preview reported. Confirm that default mode retained
branches, soft mode retained worktrees and branches, or hard mode deleted only
the local branches of removed worktrees, as applicable.

Read the exit status. `0` means the sweep did everything it planned, reported
skips and unverified worktrees included. `1` means trunk could not be resolved,
or a branch deletion failed after its worktree was removed. `2` means the
command could not run, including an invalid flag combination. A skip and an
unverified worktree are not errors, and a failed branch deletion is not a skip;
a run in which every candidate was skipped or left unverified lists those
decisions rather than reporting that no matching worktrees were found.

If the GitHub fallback was part of the run, confirm from the preview and the
summary which candidates it resolved. Do not infer a merge from a branch name,
a closed issue, or a deleted upstream alone. If `gh` was missing or failed, the
affected candidates remain unverified unless another tier completes them, and
otherwise remain on disk; treat that as a reason to investigate, not as
permission to force a removal.

Cleanup can partially succeed before a later removal fails. Report completed
and failed actions separately, re-inspect state, and stop rather than retrying
the whole batch blindly. Do not claim that Git can recover deleted untracked or
ignored files, or that a failed command rolled earlier removals back.
