"""Implement the git-donkey-template command.

Provides template directory management for git-donkey worktree templates.

Usage
-----
Run with::

    git-donkey-template

Displays the template directory path for the current repository.
"""

from __future__ import annotations

from git import InvalidGitRepositoryError, Repo

from git_donkey import helpers, templates

_TEMPLATE_PREFIX = "[git-donkey-template]"


def run_git_donkey_template() -> int:
    """Run the git-donkey-template command.

    Displays the template directory path for the current repository and creates
    it if it doesn't exist.

    Returns
    -------
    int
        The desired process exit code.

    """
    try:
        repo = Repo(".", search_parent_directories=True)
    except InvalidGitRepositoryError as exc:
        helpers._die(_TEMPLATE_PREFIX, f"not in a git repository: {exc}", 1)

    try:
        template_dir = templates.get_template_dir_path(repo)
    except ValueError as exc:
        helpers._die(_TEMPLATE_PREFIX, str(exc), 1)
        return 1  # pragma: no cover

    if template_dir is None:
        helpers._die(
            _TEMPLATE_PREFIX,
            "repository has no remote (cannot determine template path)",
            1,
        )
        # Type narrowing: unreachable after _die, but helps type checker
        return 1  # pragma: no cover

    # Create the directory if it doesn't exist
    template_dir.mkdir(parents=True, exist_ok=True)

    print(f"Template directory: {template_dir}")

    if not any(template_dir.iterdir()):
        helpers._eprint(
            "Template directory is empty. Add files here to apply them to new "
            "worktrees."
        )

    return 0
