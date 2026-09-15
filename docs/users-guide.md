# git-donkey Users' Guide

## Command overview

git-donkey ships Git subcommands, exposed as console entrypoints. Git invokes
these as `git <subcommand>` when `git-<subcommand>` is available on the `PATH`.

- `git donkey` (`git-donkey`) creates linked worktrees for branch-based work.
- `git track` (`git-track`) fetches the first remote and switches to or creates
  a tracking branch.
- `git incoming` (`git-incoming`) and `git in` (`git-in`) show commits that
  would be pulled from the current branch's upstream.
- `git outgoing` (`git-outgoing`) and `git out` (`git-out`) show commits that
  would be pushed to the current branch's upstream.
- `git fafo` (`git-fafo`) scaffolds and publishes a new GitHub repository from
  a template.
- `git plonk` (`git-plonk`) removes completed, clean worktrees or generated
  directories from worktrees created by `git donkey`, and reports any completed
  worktree it leaves in place.
- `git wheresat` (`git-wheresat`) prints the boundary a stacked branch should
  be rebased onto, so work already landed in the trunk is dropped, and changes
  nothing in the repository.
- `git donkey-template` (`git-donkey-template`) displays and creates the
  template directory for the current repository.

## Installation and manual pages

Install the commands and their manual pages into an isolated uv tool
environment:

```shell
uv tool install git-donkey
```

For an unreleased source checkout, run `uv tool install .` from the repository
root instead. The wheel includes a section-one manual for every console script:
`git-donkey(1)`, `git-track(1)`, `git-fafo(1)`, `git-plonk(1)`,
`git-wheresat(1)`, `git-donkey-template(1)`, `git-incoming(1)`, `git-in(1)`,
`git-outgoing(1)`, and `git-out(1)`.

### Installation layout

The build generates the pages from the reStructuredText sources in `docs/man/`
and stores them in the wheel's standard data directory:

```plaintext
git_donkey-<version>.data/data/share/man/man1/<command>.1
```

A wheel installer places these files under the installation environment's data
prefix, normally `<environment>/share/man/man1/`. For `uv tool install`, the
result is:

```plaintext
<uv-tool-dir>/git-donkey/share/man/man1/git-donkey.1
<uv-tool-dir>/git-donkey/share/man/man1/git-track.1
<uv-tool-dir>/git-donkey/share/man/man1/git-fafo.1
<uv-tool-dir>/git-donkey/share/man/man1/git-plonk.1
<uv-tool-dir>/git-donkey/share/man/man1/git-wheresat.1
<uv-tool-dir>/git-donkey/share/man/man1/git-donkey-template.1
<uv-tool-dir>/git-donkey/share/man/man1/git-incoming.1
<uv-tool-dir>/git-donkey/share/man/man1/git-in.1
<uv-tool-dir>/git-donkey/share/man/man1/git-outgoing.1
<uv-tool-dir>/git-donkey/share/man/man1/git-out.1
```

Use `uv tool dir` to discover the tool directory rather than assume a
particular home-directory layout. This also respects a configured `UV_TOOL_DIR`.

### Reading the installed pages

With man-db on Linux, read an installed page directly without creating symlinks:

```shell
man -l "$(uv tool dir)/git-donkey/share/man/man1/git-donkey.1"
```

Alternatively, select the tool environment's manual directory explicitly:

```shell
man -M "$(uv tool dir)/git-donkey/share/man" git-plonk
```

These commands require a manual-page viewer on the host. Building and
installing the Python package do not require `man`, `groff`, or Pandoc.

### User-manpath discovery

The package installs pages inside its environment; it does not publish them to
`$XDG_DATA_HOME/man`, modify `MANPATH`, or run a post-install hook. Therefore,
`man git-donkey` alone may not find the installed page.

User-manpath discovery and symlink management belong to the separate
`uv-tools-manpage-discover` tool, which this package does not implement or
install. Its intended destination is
`${XDG_DATA_HOME:-$HOME/.local/share}/man/man1/`. Until that tool is available,
use one of the explicit lookup commands above.

`uv tool uninstall git-donkey` removes the tool environment and its manual
pages. Any separately created user-manpath symlinks need separate lifecycle
management.

Contributor build and validation details appear in the
[manpage packaging design](manpages-design.md).

## git donkey

Create a linked worktree at `../{repo}.worktrees/{branch}`. When no base is
specified, the command discovers the default branch advertised by the
principal remote and creates the new branch from its fetched remote commit.
The principal remote is the first configured remote, preserving the existing
selection rule. The default branch need not be named `main`, and the remote
need not be named `origin`.

The command fetches remote references but does not pull, rebase, or prompt to
update a local base by default. Unpublished local commits and uncommitted
changes in the primary checkout are not used as the implicit base. An
unavailable remote default produces an error asking for an explicit base;
the command never silently falls back to local `main`.

A named base still selects that branch. `.` selects the branch checked out in
the calling working directory, including when called from a linked worktree.
Existing local or remote feature branches are reused with their existing
tracking rules; the base is used only when creating a new branch. If the
worktree path already exists or the branch is already checked out elsewhere,
the command exits with a ⚔️ conflict message.

```shell
# Create from the principal remote's default branch without updating checkouts

git donkey feature/foo

# Use the calling worktree's local branch, including its local commits

git donkey feature/foo .

# Create from a specific base without pulling it

git donkey feature/foo release/1.2

# Enable the existing confirmation workflow for pull plus rebase

git donkey feature/foo release/1.2 --pull-rebase

# Enable the same workflow, permitting only a fast-forward update

git donkey feature/foo release/1.2 --pull-ff
```

Options:

- `--pull-rebase` enables a prompt to run `git pull --rebase` when the local
  base is behind its remote counterpart.
- `--pull-ff` enables that prompt using `git pull --no-rebase --ff-only`.
  Divergent histories fail rather than being merged or rebased, regardless of
  configured pull preferences.
- `--no-pull` remains supported for compatibility and explicitly selects the
  new default behaviour. Fetching remote references still occurs.

These three options are mutually exclusive. A declined prompt, or a
non-interactive terminal, skips the update as before. A local-only base has
no remote update to perform. An approved update runs only in the worktree
holding the selected local base; if that branch is not checked out, the
command fails rather than updating an unrelated primary-checkout branch.

With an omitted base, an enabled pull option may update the corresponding
local default branch, but the new feature branch still starts at the remote
commit. Supply the local base explicitly, or use `.`, to include local
commits after an approved update.

The [default-base and pull-mode design](default-base-and-pull-modes.md)
records the discovery, preservation, and verification contracts. The
[0.2.0 migration guide](v0-2-0-migration-guide.md) documents the behaviour
changes for users upgrading from 0.1.0.

### Template Overlays

After creating the worktree, `git donkey` automatically applies template
overlay files if a template directory exists for the repository. Template
directories are stored under the platform-specific user data directory for
git-donkey:

```text
<user-data-dir>/git-donkey/template/<repo-url-slug>
```

The slug format is `<slugified-text>-<adler32-checksum>`, where the checksum
provides collision resistance while the slugified text remains human-readable.

Use `git donkey-template` within a repository to display and create the
template directory path:

```shell
cd ~/projects/myrepo
git donkey-template
# Template directory: /path/to/user-data/git-donkey/template/myrepo-a1b2c3d4
```

When a template exists, all files from the template directory are copied into
the newly created worktree. If a file already exists in the worktree, a warning
is issued, but the file is overwritten. This design allows maintaining
per-repository configuration files (such as `.editorconfig`,
`.vscode/settings.json`, or project-specific configurations) and automatically
applying them to all new worktrees.

Example template structure:

```text
<user-data-dir>/git-donkey/template/
  myrepo-a1b2c3d4/
    .editorconfig
    .vscode/
      settings.json
    config/
      local.json
```

On other platforms, the template directory follows the platform's conventions
for user data storage (e.g., `~/.local/share/git-donkey/template` on Linux or
`~/Library/Application Support/git-donkey/template` on macOS).

### Stack records at branch birth

When `git donkey` creates a branch from a base that is not the trunk, it writes
a stack record at the moment of birth. The record is four configuration keys in
the branch's own section — `branch.<branch>.stackParent`, `.stackBase`,
`.stackBaseRecordedFrom`, and `.stackBaseEvidence` — together with the anchor
ref `refs/stack-bases/<branch>`, which keeps the boundary commit reachable from
`git gc`. `stackBase` and `stackBaseRecordedFrom` are both the commit the new
branch was created at, `stackBaseEvidence` is `stack-record-birth`, and
`stackParent` names the base branch it was selected from as `v1:branch:<name>`.

A branch created from the trunk is not recorded, however it is named. That is
deliberate: a record would make it look stacked, and would offer a boundary
for a branch that never had a parent.

Writing the record changes nothing about tracking — the new branch is still
created with `--no-track` and inherits nothing. The record is local to one
clone, because neither the configuration keys nor the anchor ref are pushed or
fetched, and `git plonk` already turns it into a tombstone when it deletes the
branch (see [`git plonk`](#git-plonk)); `git wheresat` treats it as boundary
evidence. Its value today is that the boundary commit observed at birth is
preserved rather than reconstructed by forensics.
The [shared stack record](stack-records.md) design documents the full
contract.

## git track

Fetch the first remote, then switch to or create a tracking branch from
`remote/branch`. If the local branch already exists, `git track` checks it out
and merges from the remote branch. If the remote branch does not exist, it
suggests close matches.

```shell
# Switch to an existing local branch and update it

git track feature/foo
```

## git incoming and git outgoing

Preview branch movement before pulling or pushing. These commands intentionally
match the core Mercurial `incoming` and `outgoing` semantics while using Git
branches in place of Mercurial bookmarks:

- `git incoming` and `git in` show commits reachable from the comparison ref
  and not reachable from `HEAD`.
- `git outgoing` and `git out` show commits reachable from `HEAD` and not
  reachable from the comparison ref.

When no ref is provided, the comparison ref is the current branch's configured
upstream, such as `origin/feature/foo`. If no upstream is configured, the
command exits with code `2` and explains how to set an upstream or pass a ref.

```shell


# Fetch the upstream remote and show commits that would be pulled

git incoming
git in


# Fetch the upstream remote and show commits that would be pushed

git outgoing
git out
```

Pass an explicit ref to compare against something other than the current
branch's upstream:

```shell
git incoming origin/main
git outgoing origin/release/1.2
```

By default, remote-backed comparison refs are fetched before comparison. Use
`--no-fetch` to compare against the currently known local tracking ref without
contacting the remote:

```shell
git incoming --no-fetch
git outgoing origin/main --no-fetch
```

Return codes `0` and `1` follow Mercurial's documented behaviour for these
commands, while `2` is git-donkey's own code for a command that could not run:

- `0` means matching incoming or outgoing commits were found and printed.
- `1` means no matching commits were found.
- `2` means the command could not run, such as when no upstream is configured
  and no explicit ref was provided, a configured upstream cannot be resolved,
  or when the fetch or comparison failed, so automation never mistakes a
  fetch failure for "no changes".

## git fafo

Scaffold and publish a new GitHub repository.

```shell
# Create a new Python repository named demo-repo

git fafo demo-repo python
```

```shell
# Create an empty repository named demo-repo

git fafo demo-repo
```

Requirements:

- `git`
- `copier`, when scaffolding from a language template
- A GitHub token (`GITHUB_TOKEN` or `GH_TOKEN`) *or* an interactive terminal
  for device flow

Repository creation is handled via the github3.py API, so the token must have
`repo` scope for private repositories or standard access for public ones. If no
token is available, `git fafo` starts an OAuth device flow using loctocat and
the default OAuth client ID `Ov23liD2cKOAh7xmpXKR`. Override the client ID by
setting `GIT_DONKEY_GITHUB_CLIENT_ID`, then follow the prompt to enter the code
at `https://github.com/login/device`. The access token is stored at
`~/.config/git-donkey/github-token`. Override the path with
`GIT_DONKEY_CREDENTIALS_FILE`. If the target directory already exists,
`git fafo` exits early.

When a language is provided, `git fafo` expects template repositories named
`agent-template-<language>` under the current GitHub account and scaffolds the
project with Copier. When the language is omitted, `git fafo` creates an empty
local directory, initializes Git, creates the remote repository, and pushes the
empty initial commit.

If the GitHub repository already exists, `git fafo` can adopt it only when the
remote has no commits or only an empty initial commit. The command prompts
before adopting an existing repository. Use `--yes` or `-y` to confirm adoption
non-interactively:

```shell
# Adopt an existing empty repository without prompting

git fafo demo-repo --yes
```

Existing repositories with real content still exit with a ⚔️ conflict message;
choose a new name or clear the remote first.

Some Copier templates use trusted features such as `tasks`, which can run
commands during the scaffold. Copier blocks those templates unless trust is
explicitly enabled. After reviewing the template source and confirming that its
tasks are safe to run, pass `--trust`. The option only affects template-backed
scaffolds:

```shell
# Allow a trusted Python template to run Copier tasks

git fafo demo-repo python --trust
```

## git plonk

Clean up worktrees created by `git donkey`. The command must be run inside a
Git repository. It discovers the main worktree, derives the
`../{repo}.worktrees` directory used by `git donkey`, and only operates on
linked worktrees listed by Git under that directory.

```shell
# Remove completed git-donkey worktrees

git plonk
```

Default mode removes worktrees whose branch name has a recognized completion
marker and whose marker appears in canonical trunk history. Issue branches
named like `issue-123-short-title` match commits containing `(#123)`. Roadmap
branches named like `road-1-2-3a-4-short-title` match commits containing
`(road.1.2.3a.4)` or `(road.1.2.3a.4.)`. Branches with unrecognized names or no
matching trunk history marker are left alone.

The trunk is the default branch the principal remote advertises, discovered
with `git ls-remote --symref <remote> HEAD` and fetched explicitly, exactly as
`git donkey` selects an implicit base. A stale `<remote>/HEAD` alias is never
consulted, and there is no fallback to local `main`.

Cleanup never discards work. A completed worktree holding modified, staged, or
untracked files is skipped and reported, and the sweep continues with the
remaining worktrees, so one dirty candidate cannot strand its clean siblings.
Files ignored by `.gitignore` are the exception, because Git's own removal rule
ignores them too. There is no option that forces removal, so a skipped worktree
stays on disk with its branch intact.

In hard mode, if Git refuses to delete a local branch, the command reports it
under a `Failed branch deletions:` heading and continues with the remaining
worktrees. Such a run exits with status 1, unlike a skip, which leaves the
status at 0.

Soft mode removes generated directories from all `git donkey` worktrees without
removing worktrees or branches:

```shell
git plonk --soft
```

The generated directory names are `target`, `node_modules`, `.venv`, `.tox`,
`.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `htmlcov`, `dist`, `build`, and
`coverage`.

Hard mode removes completed worktrees and deletes their matching local branches:

```shell
git plonk --hard
```

Hard mode uses the same history-marker check and the same cleanliness preflight
as default mode. The branch is deleted only after its worktree is removed, so a
skipped worktree keeps its branch. It deletes local branches only; it never
forces a removal and never deletes remote branches.

Dry-run mode prints the actions `git plonk` would take and exits without
removing generated paths, worktrees, or branches. Skips appear in the preview
too, because a preview that hid them would misrepresent the run it previews. It
can be combined with the default, soft, or hard cleanup mode:

```shell
git plonk --dry-run
git plonk --soft --dry-run
git plonk --hard --dry-run
```

Every run ends with a summary. Removals are listed under their own headings,
and each skipped worktree is listed with the reason it was left alone:

```text
git-plonk: mode=hard
Removed worktrees:
- /home/user/demo.worktrees/issue-123-fix
- /home/user/demo.worktrees/issue-321-old
Removed branches:
- issue-123-fix
Failed branch deletions:
- issue-321-old (branch deletion failed)
Skipped worktrees:
- /home/user/demo.worktrees/issue-456-dirty (uncommitted changes)
- /home/user/demo.worktrees/issue-789-gone (worktree directory is missing)
```

A run in which every candidate is skipped reports the skips rather than
claiming that no matching worktrees were found.

Hard mode also owns the end of a stack record's life. A branch that `git donkey`
created from another branch carries a
[stack record](#stack-records-at-branch-birth), and deleting it would take the
one statement about where it began with it. So before `git plonk --hard`
deletes such a branch, it writes the branch's tip to the tombstone ref
`refs/stack-tombstones/<branch>` and then clears the live record and its
anchor. A child branch that outlived its parent can still reach the commit the
parent stood at when the child was cut, which is the one commit the parent's
own history can no longer supply.

Two honest limits come with that. A tombstone preserves the tip, not the
reflog, so it restores the parent's identity but not fork-point recovery:
`git merge-base --fork-point` reads a reflog, and the reflog went with the
branch. And a branch deleted through Git alone, by `git branch -D` rather than
by `git plonk --hard`, takes its whole configuration section with it, leaving
an anchor ref that names a base but no tip.

That last case belongs to the sweep. Before a completed run touches a
worktree, it clears the records of branches that no longer exist. A record
that still parses becomes a tombstone naming the tip it recorded, and an
orphan whose configuration went with the branch is cleared without inventing
anything in its place. The summary keeps the two apart, because reporting them
alike would claim a rescue that did not happen:

```text
Entombed branches:
- issue-123-fix
Swept records (tip preserved):
- issue-100-parent
Swept records (no tip to preserve):
- issue-101-plain-deleted
Pruned tombstones (older than 90.days.ago):
- issue-050-stale
```

Tombstones do not accumulate. Every completed run prunes the tombstones
written before `stack.tombstoneExpire`, a Git date expression defaulting to
`90.days.ago`, which is the same horizon as Git's own `gc.reflogExpire`. A
value Git cannot parse stops the run before anything is touched, because Git
reads an unparsable date as *now* and would prune every tombstone in the
repository. The [shared stack record](stack-records.md) design documents the
full lifecycle.

## git wheresat

`git wheresat` answers "where should this branch be rebased onto?" for a
stacked branch. It locates the exclusive replay boundary — the commit before
the branch's own work — and prints a replay plan: a backup ref for the child
tip, then `git rebase --onto <target> <old-base> <branch>` with the target and
the boundary in full, so the replay can be checked before it is run and undone
from that ref if it is wrong. Work already landed in the trunk (a squash merge,
for example) is dropped by that replay.

```shell
# Report the replay boundary of the branch checked out here

git wheresat

# Report the boundary of another branch by name

git wheresat --branch issue-123-fix
```

Without `--record` the command is read-only: it never moves a branch, never
changes a worktree, and writes no record. The only refs it may add are evidence
refs under `refs/wheresat/`; a boundary that nothing else reaches is retained at
`refs/wheresat/boundary/<branch>` so a later `git gc` cannot collect it.

It exits with one of four statuses:

| Status | Meaning                                                                                                      |
| ------ | ------------------------------------------------------------------------------------------------------------ |
| `0`    | established: a boundary was found                                                                            |
| `1`    | unresolved: the evidence refused a boundary, and the report says which check refused it                      |
| `2`    | a usage, environment, or credential error                                                                    |
| `3`    | indeterminate: the repository could not answer a question the procedure asked, so no answer is claimed       |

*Table 1: the four exit statuses.*

`--json` prints a versioned envelope (`"schema": "git-wheresat/1"`) on every
exit status, refusals included.

Options:

- `--branch` names the branch to read; it defaults to the branch checked out
  where the command runs, and a detached HEAD needs it named.
- `--onto` names the replay target; it defaults to the principal remote's
  default branch, resolved locally from `refs/remotes/<remote>/HEAD`, and is
  required when the repository has no such alias.
- `--parent OWNER/REPO#N` names a parent pull request to weigh as attested
  evidence.
- `--remote` names the principal remote to read.
- `--limit` is accepted and has no effect yet: the report's own cap of `20`
  commits per range is a fixed constant, not this option.
- `--heuristic-window` is accepted and has no effect yet; the deep scan is not
  run, and when it is, this bounds the trunk commits it examines — the target's
  history, counted backwards, read for tree-identity and
  cumulative-patch-identity evidence. It defaults to `200`.
- `--explain` prints the gate table: every check, its outcome, and the reason
  for it.
- `--json` prints the versioned envelope described above.
- `--op-id` names this run; an id that could escape the `refs/wheresat/op/`
  namespace is refused with status 2.
- `--record` refreshes the branch's [stack record](#stack-records-at-branch-birth)
  — the boundary, the tip the record was written from, and the evidence kind
  that says a run restated it — instead of only reading it. A refresh never
  invents a record: a branch no one recorded is reported, not recorded. It also
  writes only a boundary the run established from attested evidence, so a run
  that had to derive one still reports the boundary and warns that nothing was
  recorded.
- `--expected-old` names the commit the record's anchor ref must hold for
  `--record` to replace it. It is required when the anchor ref exists — a write
  that names no expectation is refused with status 2 rather than replacing a
  record the user did not read — and must not be given when it does not, because
  re-creating a collected anchor replaces nothing. Git performs the same
  comparison again at the write, so an anchor that moves in between is refused
  rather than overwritten.

Refresh a record when its anchor ref has been collected — the configuration
still names the boundary, and the refresh writes the ref back — or when the
record should say the branch has been restated at the tip it has since reached.

```shell
# Re-anchor a record whose ref git gc collected

git wheresat --record

# Replace the record the run read, naming what its ref must hold

git wheresat --record --expected-old <commit>
```

A record goes stale when the branch's own history is rewritten under it. Once
the parent has been integrated and the branch restacked onto it, the tip the
record was written from is no longer on the branch, so the record stops being a
claim about where the branch came from: the run reads it as derived evidence,
answers with the boundary the surviving history agrees on, and `--record` writes
nothing back. Only an attested claim is written back, and the record's own claim
is the only attested source the local path has, so a refresh restates the
boundary the record already names — it re-anchors the commit and restates the
tip the branch is at, so a later reader can tell the record was restated by a
run rather than left as the claim written at birth. Moving a record to a new
boundary needs an attested account of where the parent went, which local
evidence cannot give.

`--no-fetch`, `--offline`, and `--deep` are accepted and change no answer yet:
the local-evidence path fetches nothing and compares nothing deeply,
so the forge-backed evidence and the deeper comparisons those options control
are not wired yet. A named `--parent` still answers with status 3, because the
parent pull request cannot be consulted.

Evidence is weighted in tiers: attested (the stack record `git donkey` wrote
at the branch's birth, a refreshed record, a fetched parent pull request
head), derived (the merge base, the fork point), and inferred (tree identity,
patch identity). Only attested and derived evidence can establish a boundary;
inferred evidence is reported but never decides. `--explain` shows the tier of
each line of evidence and the eight named checks.

Warnings change neither the verdict nor the exit status. The run warns when
the worktree holding the branch has uncommitted changes, and when a rebase,
merge, cherry-pick, revert, or bisect is already in progress there. A
worktree whose state cannot be read is warned about too, because a run that
warned about nothing would be read as a run with nothing to warn about.

The two honest limits documented under [`git plonk`](#git-plonk) still bound
fork-point recovery here: a tombstone preserves a deleted branch's tip and not
its reflog, so fork-point recovery for its children is still lost.

## git donkey-template

Display and create the template directory for the current repository. Template
files placed in this directory are automatically copied to new worktrees
created by `git donkey`.

```shell
# Display template directory path (creates directory if needed)

git donkey-template
```

The command must be run from within a Git repository. It creates the template
directory if it doesn't exist and displays its path. The template directory is
specific to the repository's remote URL, so different repositories (or
repositories with different remote URLs) have separate template directories. If
multiple remotes are configured and none is named `origin`, the command exits
with an error; rename a remote to `origin` or remove the extra remotes to
resolve the ambiguity.

## Agent skill

The [worktree-management skill](../skill/git-donkey-worktrees/SKILL.md) is
written for coding agents, and their operators, that create, reuse, and
retire linked worktrees with `git donkey` and `git plonk`. It is not needed
for ordinary editing in a checkout already selected for a task.

### Prerequisites

The skill requires Git plus `git-donkey` and `git-plonk` on `PATH`. It expects
`git donkey` to support remote-default bases, `--no-pull`, `--pull-ff`, and
`--pull-rebase`, and `git plonk` to support `--soft`, `--hard`, and
`--dry-run`, whose help text describes skipping worktrees that hold
uncommitted or untracked files rather than forcing their removal, as older
releases did. The skill tells the agent to check `git donkey --help` and
`git plonk --help` before relying on these behaviours.

### When it activates

The skill applies to creating an isolated branch checkout, locating or
reusing an existing worktree, preparing a stacked branch, retiring completed
worktrees, or explicitly cleaning generated directories. It does not apply to
ordinary editing, committing, or pull-request review.

### Installation

Copy or symlink the complete `skill/git-donkey-worktrees/` directory into the
agent's documented skill location, keeping the directory name and its bundled
`references/` subdirectory intact. The Python package does not install the
skill automatically; installation is agent-specific.

```shell
# Copy the skill into an agent's skill directory
cp -r skill/git-donkey-worktrees ~/.agents/skills/

# Or symlink it instead of copying
ln -s "$(pwd)/skill/git-donkey-worktrees" ~/.agents/skills/git-donkey-worktrees
```

### Supported workflows

The skill covers:

- creating a worktree from a remote-default base, an explicit base, or `.`,
- opting in to a pull with `--pull-ff` or `--pull-rebase`,
- reusing an existing branch, including its upstream constraints,
- verifying the resulting worktree's path, branch, and starting commit,
- applying template overlays copied into new worktrees,
- handing a stacked branch over to a child worktree, and
- reviewed cleanup that starts with the matching `--dry-run` preview and
  requires reading the bundled cleanup checklist first.

### Cleanup safeguards beyond the commands themselves

The bundled
[cleanup checklist](../skill/git-donkey-worktrees/references/cleanup.md) adds
safeguards that `git plonk` itself does not enforce:

- authorization for the entire preview batch, not just individual candidates,
- per-candidate checks of data and ownership before removal,
- running the preview and the execution from the same invoking worktree,
- verification of the resulting state after the run, and
- reporting of skipped worktrees, failed branch deletions, and exit statuses.
