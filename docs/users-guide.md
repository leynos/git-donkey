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
- `git plonk` (`git-plonk`) removes completed worktrees or generated
  directories from worktrees created by `git donkey`.
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
`git-donkey(1)`, `git-track(1)`, `git-fafo(1)`, `git-plonk(1)`, and
`git-donkey-template(1)`.

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

Return codes follow Mercurial's documented behaviour for these commands:

- `0` means matching incoming or outgoing commits were found and printed.
- `1` means no matching commits were found.
- `2` means the command could not run, such as when no upstream is configured
  and no explicit ref was provided, or when the fetch or comparison failed, so
  automation never mistakes a fetch failure for "no changes".

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
marker and whose marker appears in the canonical trunk history used by
`git plonk`. Issue branches named like `issue-123-short-title` match commits
containing `(#123)`. Roadmap branches named like `road-1-2-3a-4-short-title`
match commits containing `(road.1.2.3a.4)` or `(road.1.2.3a.4.)`. Branches with
unrecognized names or no matching trunk history marker are left alone.

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

Hard mode uses the same history-marker check as default mode before deleting a
branch. It deletes local branches only; it never deletes remote branches.

Dry-run mode prints the actions `git plonk` would take and exits without
removing generated paths, worktrees, or branches. It can be combined with the
default, soft, or hard cleanup mode:

```shell
git plonk --dry-run
git plonk --soft --dry-run
git plonk --hard --dry-run
```

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
