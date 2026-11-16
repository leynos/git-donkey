"""Git repository builders shared by the unit and integration suites.

The behaviour these tests pin — which branch a remote advertises as its default,
and what ``git worktree remove`` refuses to discard — is Git's own. A Python
double for ``Repo`` would assert the test author's belief about Git rather than
Git itself, so these helpers build the smallest real repository that exhibits
the behaviour, configuring each one with a local commit identity so tests never
depend on, or write to, the runner's global Git configuration.
"""

from __future__ import annotations

import typing as typ

from git import Repo

if typ.TYPE_CHECKING:
    from pathlib import Path


def configure_repo(repo: Repo) -> None:
    """Configure the commit identity required to create commits.

    Parameters
    ----------
    repo : Repo
        Repository to configure. Its local configuration is modified.

    """
    with repo.config_writer() as config:
        config.set_value("user", "name", "Test User")
        config.set_value("user", "email", "test@example.com")


def seed_repo(repo_path: Path, *, branch: str = "main") -> Repo:
    """Create a repository holding one seed commit.

    Parameters
    ----------
    repo_path : Path
        Directory to create the repository in.
    branch : str, optional
        Name for the branch holding the seed commit.

    Returns
    -------
    Repo
        The repository, checked out on ``branch``.

    """
    repo = Repo.init(repo_path)
    configure_repo(repo)
    seed_path = repo_path / "README.md"
    seed_path.write_text("seed")
    repo.index.add([seed_path.as_posix()])
    repo.index.commit("Seed commit")
    repo.git.branch("-M", branch)
    return repo


def repo_with_remote_default(
    repo_path: Path,
    remote_path: Path,
    *,
    default_branch: str = "main",
) -> tuple[Repo, Repo]:
    """Create a repository whose principal remote advertises ``default_branch``.

    The local repository keeps its seed commit on ``main`` whatever the remote
    advertises, so callers can observe that discovery follows the remote rather
    than a locally familiar branch name.

    Parameters
    ----------
    repo_path : Path
        Directory to create the working repository in.
    remote_path : Path
        Directory to create the bare remote in.
    default_branch : str, optional
        Branch the remote's symbolic ``HEAD`` points at.

    Returns
    -------
    tuple[Repo, Repo]
        The working repository and the bare remote it pushes to.

    """
    remote_repo = Repo.init(remote_path, bare=True)
    remote_repo.git.symbolic_ref("HEAD", f"refs/heads/{default_branch}")
    repo = seed_repo(repo_path)
    repo.create_remote("origin", remote_path.as_posix())
    repo.remote("origin").push(f"main:refs/heads/{default_branch}")
    return repo, remote_repo


FETCH_ALL_BRANCHES_REFSPEC = "+refs/heads/*:refs/remotes/origin/*"


def bare_collection(
    container_path: Path,
    remote_path: Path,
    *,
    bare_name: str = "bare.git",
    session_branch: str = "session/one",
) -> tuple[Repo, Path]:
    """Create a bare repository with one linked worktree beside it.

    The collection mirrors the layout a session manager such as Lody keeps:
    ``<container>/<bare_name>`` holds the repository and
    ``<container>/worktrees/<id>`` holds a checkout, so no main worktree
    exists. ``git clone --bare`` configures no fetch refspec, so the helper
    adds the conventional one; a test that needs the refspec absent removes
    it again.

    Parameters
    ----------
    container_path : Path
        Directory the bare repository and its ``worktrees`` directory are
        created in.
    remote_path : Path
        Existing bare remote, advertising a default branch, to clone from.
    bare_name : str, optional
        Directory name of the bare repository inside the container.
    session_branch : str, optional
        Branch created for, and checked out in, the linked worktree.

    Returns
    -------
    tuple[Repo, Path]
        The bare repository and the path of its linked worktree.

    """
    bare_repo = Repo.clone_from(
        remote_path.as_posix(),
        container_path / bare_name,
        bare=True,
    )
    configure_repo(bare_repo)
    with bare_repo.config_writer() as config:
        config.set_value('remote "origin"', "fetch", FETCH_ALL_BRANCHES_REFSPEC)
    bare_repo.remote("origin").fetch()
    session_path = container_path / "worktrees" / "session-one"
    bare_repo.git.worktree(
        "add",
        "-b",
        session_branch,
        session_path.as_posix(),
        bare_repo.head.commit.hexsha,
    )
    return bare_repo, session_path
