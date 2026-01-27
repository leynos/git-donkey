# 🫏 git-donkey

*Because sometimes you need a worktree, and a donkey's got your back.*

git-donkey is a collection of Git subcommands that make branch-based
development less of a… well, you know. Stop wrestling with worktrees, tracking
branches, and repository scaffolding—let the donkey do the heavy lifting.

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
options, and examples. The donkey's got a lot of tricks up its… hooves?

## License

This project is licensed under the **ISC License**. See the [LICENSE](LICENSE)
file for details.

## Contributing

Found a bug? Have an idea? The donkey welcomes contributions! Please ensure
your changes follow the project's guidelines in [AGENTS.md](AGENTS.md).
