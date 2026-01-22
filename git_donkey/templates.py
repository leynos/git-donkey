"""Template overlay management for git-donkey.

Provides functionality to apply overlay templates to worktrees. Templates are
trees of files stored in ~/.local/share/git-donkey/template/<repo-slug>/<branch-slug>
that are copied into the worktree after it is created.
"""

from __future__ import annotations

import shutil
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    from git import Repo

from git_donkey import helpers, slugs


def _get_template_base_dir() -> Path:
    """Return the base directory for template storage.

    Returns
    -------
    Path
        The base template directory: ~/.local/share/git-donkey/template

    """
    home = Path.home()
    return home / ".local" / "share" / "git-donkey" / "template"


def _get_repo_url(repo: Repo) -> str | None:
    """Get the remote URL for the repository.

    Parameters
    ----------
    repo : Repo
        The Git repository.

    Returns
    -------
    str | None
        The remote URL if available, otherwise None.

    """
    if not repo.remotes:
        return None
    # Use the first remote's URL
    return repo.remotes[0].url


def get_template_dir(repo: Repo, branch_name: str) -> Path | None:
    """Get the template directory for a given repository and branch.

    Parameters
    ----------
    repo : Repo
        The Git repository.
    branch_name : str
        The branch name.

    Returns
    -------
    Path | None
        The template directory path if it exists, otherwise None.

    """
    repo_url = _get_repo_url(repo)
    if repo_url is None:
        return None

    repo_slug = slugs.slug_dash_adler32(repo_url)
    branch_slug = slugs.slug_dash_adler32(branch_name)

    template_dir = _get_template_base_dir() / repo_slug / branch_slug

    if template_dir.exists() and template_dir.is_dir():
        return template_dir
    return None


def apply_template(
    template_dir: Path,
    target_dir: Path,
    *,
    prefix: str,
) -> list[Path]:
    """Apply a template overlay to a target directory.

    Copies all files from the template directory to the target directory.
    Warns if any file already exists in the target but copies anyway.

    Parameters
    ----------
    template_dir : Path
        The source template directory.
    target_dir : Path
        The target directory to copy files into.
    prefix : str
        Prefix for error/warning messages.

    Returns
    -------
    list[Path]
        List of files that already existed in the target (warnings issued).

    Raises
    ------
    ValueError
        If template_dir does not exist or is not a directory.

    """
    if not template_dir.exists():
        msg = f"Template directory does not exist: {template_dir}"
        raise ValueError(msg)
    if not template_dir.is_dir():
        msg = f"Template path is not a directory: {template_dir}"
        raise ValueError(msg)

    conflicts: list[Path] = []

    # Walk through all files in the template directory
    for template_file in template_dir.rglob("*"):
        if template_file.is_file():
            # Calculate relative path from template_dir
            rel_path = template_file.relative_to(template_dir)
            target_file = target_dir / rel_path

            # Check if file already exists
            if target_file.exists():
                helpers._eprint(
                    f"{prefix} Warning: file already exists, overwriting: {rel_path}"
                )
                conflicts.append(rel_path)

            # Create parent directories if needed
            target_file.parent.mkdir(parents=True, exist_ok=True)

            # Copy the file
            shutil.copy2(template_file, target_file)

    return conflicts
