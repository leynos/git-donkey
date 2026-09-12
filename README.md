# 🫏 git-donkey

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](
https://deepwiki.com/leynos/git-donkey)

*Because sometimes you need a worktree, and a donkey's got your back.*

git-donkey is a collection of Git subcommands that make branch-based
development less of a… well, you know. Stop wrestling with worktrees, tracking
branches, and repository scaffolding—let the donkey do the heavy lifting.

## What does it do?

git-donkey gives you Git subcommands for branch-based work:

- **`git donkey`** – Creates linked worktrees at
  `../{repo}.worktrees/{branch}` so you can work on multiple branches without
  the constant stash-switch-unstash dance. Bonus: automatically applies
  template overlays from your personal template directory!

- **`git track`** – Fetch and switch to (or create) tracking branches in one
  command. No more "did I create this branch yet?" confusion.

- **`git incoming`** / **`git in`** – Preview upstream commits that would be
  pulled into the current branch.

- **`git outgoing`** / **`git out`** – Preview local commits that would be
  pushed to the current branch's upstream.

- **`git fafo`** – Scaffold and publish a new GitHub repository from your
  `agent-template` projects. Find out what happens when you want to start a new
  project *fast*.

- **`git plonk`** – Reclaim disk space from completed, clean `git donkey`
  worktrees, optionally cleaning generated directories or deleting completed
  local branches. Worktrees holding uncommitted work are reported and left
  alone rather than discarded.

- **`git donkey-template`** – Manage template directories that get
  automatically copied into new worktrees. Perfect for per-repository config
  files like `.editorconfig` or `.vscode/settings.json`.

## Quick start

```shell
# Create a worktree from the principal remote's default branch
# Existing checkouts are not pulled or rebased by default.
git donkey feature/awesome-stuff

# Opt in to a fast-forward-only update of an explicit local base
git donkey feature/from-main main --pull-ff

# Track a remote branch
git track feature/from-teammate


# Preview pull and push candidates
git incoming
git outgoing

# Scaffold a new GitHub repo from a template
git fafo my-new-project python

# Remove completed git-donkey worktrees
git plonk

# Set up a template directory for this repo
git donkey-template
```

## Learn more

Check out the [**Users' Guide**](docs/users-guide.md) for detailed usage,
options, and examples. The donkey's got a lot of tricks up its… hooves?

## License

This project is licensed under the **ISC License**. See the [LICENSE](LICENSE)
file for details.

## Contributing

Found a bug? Have an idea? The donkey welcomes contributions! Please ensure
your changes follow the project's guidelines in [AGENTS.md](AGENTS.md).
