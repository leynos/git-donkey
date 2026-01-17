# git-donkey Users' Guide

## Command overview

git-donkey ships three Git subcommands, exposed as console entrypoints. Git
invokes these as `git <subcommand>` when `git-<subcommand>` is available on the
`PATH`.

- `git donkey` (`git-donkey`) creates linked worktrees for branch-based work.
- `git track` (`git-track`) fetches the first remote and switches to or creates
  a tracking branch.
- `git fafo` (`git-fafo`) scaffolds and publishes a new GitHub repository from
  a template.

## git donkey

Create a linked worktree at `../{repo}.worktrees/{branch}`, branching from
`main` by default, a named base branch, or the branch checked out in the
current working directory. The command uses the first Git remote, reuses an
existing local or remote branch when present, and prompts to pull --rebase the
base branch if it is behind (unless `--no-pull` is set). If the worktree path
already exists or the branch is already checked out elsewhere, the command
exits with a ⚔️ conflict message. If the base branch has no remote counterpart,
the behind check is skipped.

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

Scaffold and publish a new GitHub repository from an `agent-template` project.

```shell
# Create a new Python repository named demo-repo

git fafo demo-repo python
```

Requirements:

- `git`
- `copier`
- A GitHub token (`GITHUB_TOKEN` or `GH_TOKEN`) *or* an interactive terminal
  for device flow

Repository creation is handled via the github3.py API, so the token must have
`repo` scope for private repositories or standard access for public ones. If no
token is available, `git fafo` starts an OAuth device flow using loctocat and
the default OAuth client ID `Ov23liD2cKOAh7xmpXKR`. Override the client ID by
setting `GIT_DONKEY_GITHUB_CLIENT_ID`, then follow the prompt to enter the code
at `https://github.com/login/device`. The access token is stored at
`~/.config/git-donkey/github-token`. Override the path with
`GIT_DONKEY_CREDENTIALS_FILE`. If the GitHub repository already exists,
`git fafo` exits with a ⚔️ conflict message. If the target directory already
exists, `git fafo` exits early.

`git fafo` expects template repositories named `agent-template-<language>`
under the current GitHub account.
