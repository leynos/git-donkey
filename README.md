# 🫏 git-donkey

*For projects that require a worktree, git-donkey provides support.*

git-donkey is a collection of Git subcommands that make branch-based
development easier. It eliminates manual work with worktrees, branch tracking,
and repository scaffolding by automating these tasks.

## What does it do?

git-donkey gives you four powerful Git subcommands:

- **`git donkey`** – Creates linked worktrees at
  `../{repo}.worktrees/{branch}` so you can work on multiple branches without
  the constant stash-switch-unstash dance. Bonus: automatically applies
  template overlays from your personal template directory!

- **`git track`** – Fetch and switch to (or create) tracking branches in one
  command. No more "did I create this branch yet?" confusion.

- **`git fafo`** – Scaffold and publish a new GitHub repository from your
  `agent-template` projects. Find out what happens when you want to start a new
  project *fast*.

- **`git donkey-template`** – Manage template directories that get
  automatically copied into new worktrees. Perfect for per-repository config
  files like `.editorconfig` or `.vscode/settings.json`.

## Quick start

```shell
# Create a worktree for feature/awesome-stuff from main
git donkey feature/awesome-stuff

# Track a remote branch
git track feature/from-teammate

# Scaffold a new GitHub repo from a template
git fafo my-new-project python

# Set up a template directory for this repo
git donkey-template
```

## Learn more

Check out the [**Users' Guide**](docs/users-guide.md) for detailed usage,
options, and examples.

## Licence

This project is licensed under the **ISC Licence**. See the [LICENSE](LICENSE)
file for details.

## Contributing

Found a bug or have an idea? Contributions are welcome. Changes should follow
the project's guidelines in [AGENTS.md](AGENTS.md).
