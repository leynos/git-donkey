"""Template overlay management for git-donkey.

Provides functionality to apply overlay templates to worktrees. Templates are
trees of files stored in a platform-specific directory under
<user-data-dir>/git-donkey/template/<repo-slug> that are copied into the
worktree after it is created.
"""

from __future__ import annotations

import shutil
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    from git import Repo

import platformdirs

from git_donkey import helpers, slugs


def _get_template_base_dir() -> Path:
    """Return the base directory for template storage.

    The base directory is derived from the platform-specific user data
    directory for the ``git-donkey`` application, with ``"template"``
    appended.

    On most Linux systems this corresponds to
    ``$XDG_DATA_HOME/git-donkey/template`` (or the appropriate default),
    while on other platforms it resolves to the conventional user data
    location as determined by ``platformdirs``.

    Returns
    -------
    Path
        The base template directory.

    """
    base_dir = Path(platformdirs.user_data_dir("git-donkey"))
    return base_dir / "template"


def _get_repo_url(repo: Repo) -> str | None:
    """Get the remote URL for the repository.

    Prefer the ``origin`` remote when available, to ensure consistent behavior
    across clones. Fall back to the first configured remote if no ``origin``
    remote is present.

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

    # Prefer 'origin' when available to avoid depending on remote creation order.
    origin_remote = next(
        (remote for remote in repo.remotes if remote.name == "origin"), None
    )
    remote = origin_remote or repo.remotes[0]
    return remote.url


def get_template_dir_path(repo: Repo) -> Path | None:
    """Get the template directory path for a given repository.

    Returns the path regardless of whether the directory exists.

    Parameters
    ----------
    repo : Repo
        The Git repository.

    Returns
    -------
    Path | None
        The template directory path, or None if the repository has no remote.

    """
    repo_url = _get_repo_url(repo)
    if repo_url is None:
        return None

    repo_slug = slugs.slug_dash_adler32(repo_url)
    return _get_template_base_dir() / repo_slug


def get_template_dir(repo: Repo) -> Path | None:
    """Get the template directory for a given repository.

    Parameters
    ----------
    repo : Repo
        The Git repository.

    Returns
    -------
    Path | None
        The template directory path if it exists, otherwise None.

    """
    template_dir = get_template_dir_path(repo)
    if template_dir is None:
        return None

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
