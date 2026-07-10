# git-donkey Users' Guide

## Command overview

git-donkey ships Git subcommands, exposed as console entrypoints. Git invokes
these as `git <subcommand>` when `git-<subcommand>` is available on the `PATH`.

- `git donkey` (`git-donkey`) creates linked worktrees for branch-based work.
- `git track` (`git-track`) fetches the first remote and switches to or creates
  a tracking branch.
- `git fafo` (`git-fafo`) scaffolds and publishes a new GitHub repository from
  a template.
- `git plonk` (`git-plonk`) removes completed worktrees or generated
  directories from worktrees created by `git donkey`.
- `git donkey-template` (`git-donkey-template`) displays and creates the
  template directory for the current repository.

## git donkey

Create a linked worktree at `../{repo}.worktrees/{branch}`, branching from
`main` by default, a named base branch, or the branch checked out in the
current working directory. The command prefers the `origin` remote and falls
back to the first remote when `origin` is absent, reuses an existing local or
remote branch when present, and prompts to pull --rebase the base branch if it
is behind (unless `--no-pull` is set). If the worktree path already exists or
the branch is already checked out elsewhere, the command exits with a ⚔️
conflict message. If the base branch has no remote counterpart, the behind
check is skipped.

```shell
# Create a new worktree for feature/foo from main

git donkey feature/foo

# Use the current branch as the base for the new worktree

git donkey feature/foo .

# Create from a specific base branch without prompting for pull --rebase

git donkey feature/foo release/1.2 --no-pull
```

Options:

- `--no-pull` skips prompting to pull the base branch if it is behind the
  remote.

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
