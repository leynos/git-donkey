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
- `git donkey-template` (`git-donkey-template`) displays and creates the
  template directory for the current repository.

## Installation and manual pages

Install the commands and their manual pages into an isolated uv tool
environment:

```shell
uv tool install git-donkey
```

Released versions are published to PyPI and, with identical bytes, to the tag's
GitHub Release. To verify a downloaded wheel, use GitHub CLI 2.102.0 or newer.
This example uses the stable `v1.2.3` tag. A pre-release tag may produce a
differently spelled wheel filename, so verify the file the download actually
produced:

```shell
gh release download v1.2.3 --repo leynos/git-donkey --pattern 'git_donkey-*'
gh release download v1.2.3 --repo leynos/git-donkey --pattern '*.attestations.jsonl'
gh attestation verify git_donkey-1.2.3-py3-none-any.whl \
  --repo leynos/git-donkey \
  --signer-workflow leynos/git-donkey/.github/workflows/release.yml
```

The command verifies the file against the repository's attestation store. The
release also carries a combined attestation bundle covering every published
file. For an offline check, download the trusted roots while still online,
transfer them with the bundle, and pass both to the verifier:

```shell
gh attestation trusted-root > trusted_root.jsonl
for bundle in git-donkey-v1.2.3-*.attestations.jsonl; do
  gh attestation verify git_donkey-1.2.3-py3-none-any.whl \
    --repo leynos/git-donkey \
    --signer-workflow leynos/git-donkey/.github/workflows/release.yml \
    --bundle "${bundle}" \
    --custom-trusted-root trusted_root.jsonl && break
done
```

For an unreleased source checkout, run `uv tool install .` from the repository
root instead. The wheel includes a section-one manual for every console script:
`git-donkey(1)`, `git-track(1)`, `git-fafo(1)`, `git-plonk(1)`,
`git-donkey-template(1)`, `git-incoming(1)`, `git-in(1)`, `git-outgoing(1)`, and
`git-out(1)`.

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

Create a linked worktree at `../{repo}.worktrees/{branch}`, a sibling of the
main worktree. When no base is specified, the command discovers the default
branch advertised by the principal remote and creates the new branch from its
fetched remote commit. The principal remote is the first configured remote,
preserving the existing selection rule. The default branch need not be named
`main`, and the remote need not be named `origin`.

The command fetches remote references but does not pull, rebase, or prompt to
update a local base by default. Unpublished local commits and uncommitted
changes in the primary checkout are not used as the implicit base. An
unavailable remote default produces an error asking for an explicit base; the
command never silently falls back to local `main`.

A named base still selects that branch. `.` selects the branch checked out in
the calling working directory, including when called from a linked worktree.
Existing local or remote feature branches are reused with their existing
tracking rules; the base is used only when creating a new branch. If the
worktree path already exists or the branch is already checked out elsewhere,
the command exits with a ⚔️ conflict message.

### Bare repositories and the worktree root

`git donkey` also runs in a bare repository whose checkouts are all linked
worktrees, such as a collection that keeps `bare.git` beside a `worktrees`
directory. It can be invoked from any linked worktree or from the bare
directory itself. With no main worktree to sit beside, new worktrees are
created at `<bare-name>.worktrees/{branch}` beside the bare repository, with a
trailing `.git` dropped from the name: `demo.git` uses `demo.worktrees`, and
`bare.git` uses `bare.worktrees`. A directory another tool fills with its own
worktrees is never used, so `git plonk` cannot reach those worktrees.

Set `donkey.worktreesRoot` to choose a different directory in either kind of
repository. A relative value is resolved against the main worktree, or against
the bare repository when there is no main worktree:

```shell
git config donkey.worktreesRoot ../donkeys
```

`git plonk` cleans the same directory, so choose one that holds only worktrees
`git plonk` may remove. Changing the value does not move existing worktrees.

The principal remote must have a fetch refspec, which `git clone --bare` does
not configure. Without one `git donkey` exits with code `2` and prints the
`git config` command that adds it, because remote branches could not otherwise
be found and reused. The [worktree layout design](worktree-layouts.md) records
the reasoning.

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
non-interactive terminal, skips the update as before. A local-only base has no
remote update to perform. An approved update runs only in the worktree holding
the selected local base; if that branch is not checked out, the command fails
rather than updating an unrelated primary-checkout branch.

With an omitted base, an enabled pull option may update the corresponding local
default branch, but the new feature branch still starts at the remote commit.
Supply the local base explicitly, or use `.`, to include local commits after an
approved update.

The [default-base and pull-mode design](default-base-and-pull-modes.md) records
the discovery, preservation, and verification contracts. The
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
  or when the fetch or comparison failed, so automation never mistakes a fetch
  failure for "no changes".

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
Git repository. It resolves the worktree root `git donkey` uses, which is the
`../{repo}.worktrees` sibling of the main worktree, the `<bare-name>.worktrees`
sibling of a bare repository, or the directory named by `donkey.worktreesRoot`.
It only operates on linked worktrees listed by Git under that directory. In a
bare repository collection, worktrees another tool keeps elsewhere, such as
session worktrees, are never removed or cleaned.

```shell
# Remove completed git-donkey worktrees

git plonk
```

Default mode removes worktrees when a completion tier resolves the branch and
the worktree is clean. Three branch-name shapes are recognized offline:

- `issue-123-short-title` completes on `(#123)` in trunk history;
- df12-build roadmap branches such as `roadmap-2-2-1` and
  `roadmap-2-2-1-addendum` complete on the un-namespaced dotted id `(2.2.1)`,
  which may have two to four components; and
- legacy roadmap branches such as `road-1-2-3a-4-short-title` complete on
  `(road.1.2.3a.4)`. A task id may end the name, so `road-1-2-3a-4` works too.

An issue branch also accepts the signals GitHub writes into commit messages: a
closing keyword such as `Closes #123`, with `Fixes` and `Resolves` and their
inflections, a required separator (whitespace, or a colon with optional
surrounding whitespace), and no owner or repository prefix; a classic merge
commit such as `Merge pull request #45 from owner/issue-123-short-title` in the
subject of a real merge commit, whose branch segment matches exactly, whatever
its pull-request number; and the branch name appearing verbatim in a bounded
context.

### GitHub fallback

When no offline signal resolves a branch, `git plonk` asks GitHub whether a
merged pull request exists for it. The fallback is enabled by default when the
principal remote is GitHub and `gh` is installed. The query is read-only, and a
result is accepted only when its head branch, base branch (the advertised
trunk), head owner, and head commit match the request. The commit check keeps a
reused branch name from completing new work on the strength of an earlier
merge. A failed, timed-out, malformed, or mismatched response counts as
unknown, never merged. Pass `--no-github` to disable the fallback. A missing
`gh` is reported once as a notice and also disables it.

### Upstream-gone check

The opt-in `--upstream-gone` check treats a branch whose configured upstream no
longer exists as completed. It reads `branch.<name>.remote` and
`branch.<name>.merge` and asks
`git ls-remote --exit-code --heads <remote> refs/heads/<branch>`. Only a
deleted branch completes a candidate: an upstream that still exists, was never
configured, or cannot be queried leaves it unverified. The signal is opt-in
because a deleted branch cannot distinguish a merged change from an abandoned
one. A remote configured to hide refs with `uploadpack.hideRefs` can make an
existing upstream look deleted, so the check cannot prove absence in that case.
Neither `--no-github` nor `--upstream-gone` is valid with `--soft`, which does
not judge completion.

### Trunk and cleanup rules

The trunk is the default branch the principal remote advertises, discovered with
`git ls-remote --symref <remote> HEAD` and fetched explicitly, exactly as
`git donkey` selects an implicit base. A stale `<remote>/HEAD` alias is never
consulted, and there is no fallback to local `main`.

Cleanup never discards uncommitted worktree files. A completed worktree holding
modified, staged, or untracked files is skipped and reported, and the sweep
continues with the remaining worktrees, so one dirty candidate cannot strand
its clean siblings. Files ignored by `.gitignore` are the exception, because
Git's own removal rule ignores them too. A worktree whose branch or commit
changed after the sweep selected it is skipped as
`worktree changed during the sweep`. A worktree with a locked registration is
skipped as `worktree is locked`, including in a dry run, because Git refuses to
remove it. There is no option that forces removal, so a skipped worktree stays
on disk with its branch intact.

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

Hard mode uses the same completion tiers and the same cleanliness preflight as
default mode. The branch is deleted only after its worktree is removed, so a
skipped worktree keeps its branch. It deletes local branches only; it never
forces a removal and never deletes remote branches.

Hard mode force-deletes each matching branch, but only while it still points at
the commit the sweep recorded. A commit made to the branch during the sweep
keeps the branch and is reported as a failed deletion. A merged pull request
matched by name does not prove that every local commit on the branch was
merged, and a commit made after the merge can lose its branch reference when
the branch goes. Inspect each branch tip with `git log` before running
`git plonk --hard`.

Dry-run mode prints the actions `git plonk` would take and exits without
removing generated paths, worktrees, or branches. Skips and unverified
worktrees appear in the preview too, because a preview that hid them would
misrepresent the run it previews. It can be combined with the default, soft, or
hard cleanup mode:

```shell
git plonk --dry-run
git plonk --soft --dry-run
git plonk --hard --dry-run
```

Every run ends with a summary. Each removal names the signal that justified it,
each skipped worktree names why it was left alone, and a candidate no tier
resolved is listed as unverified with one of the fixed reasons
`no completion signal found`, `GitHub completion lookup failed`,
`no upstream branch configured`, `upstream branch still exists`, or
`upstream state unknown`:

```text
git-plonk: mode=hard
Removed worktrees:
- /home/user/demo.worktrees/issue-123-fix (completed by marker (#123))
- /home/user/demo.worktrees/fix-markdown-gates (completed by merged pull request #61)
Removed branches:
- issue-123-fix
- fix-markdown-gates
Failed branch deletions:
- issue-321-old (branch deletion failed)
Skipped worktrees:
- /home/user/demo.worktrees/issue-456-dirty (uncommitted changes)
Unverified worktrees:
- /home/user/demo.worktrees/adopt-whitaker (no completion signal found)
```

A run in which every candidate is skipped reports the skips rather than
claiming that no matching worktrees were found. Unverified worktrees are never
removed, and, like a skip, they leave the exit status at 0.

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
written for coding agents, and their operators, that create, reuse, and retire
linked worktrees with `git donkey` and `git plonk`. It is not needed for
ordinary editing in a checkout already selected for a task.

### Prerequisites

The skill requires Git plus `git-donkey` and `git-plonk` on `PATH`. It expects
`git donkey` to support remote-default bases, `--no-pull`, `--pull-ff`, and
`--pull-rebase`, and `git plonk` to support `--soft`, `--hard`, `--dry-run`,
`--no-github`, and `--upstream-gone`, whose help text describes the offline,
GitHub, and upstream completion tiers, and skipping worktrees that hold
uncommitted or untracked files rather than forcing their removal, as older
releases did. The skill tells the agent to check `git donkey --help` and
`git plonk --help` before relying on these behaviours.

### When it activates

The skill applies to creating an isolated branch checkout, locating or reusing
an existing worktree, preparing a stacked branch, retiring completed worktrees,
or explicitly cleaning generated directories. It does not apply to ordinary
editing, committing, or pull-request review.

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
