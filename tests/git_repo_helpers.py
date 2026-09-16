"""Git repository builders shared by the unit and integration suites.

The behaviour these tests pin — which branch a remote advertises as its default,
and what ``git worktree remove`` refuses to discard — is Git's own. A Python
double for ``Repo`` would assert the test author's belief about Git rather than
Git itself, so these helpers build the smallest real repository that exhibits
the behaviour, configuring each one with a local commit identity so tests never
depend on, or write to, the runner's global Git configuration.

The three stack builders at the end of the module build the shapes the boundary
recovery has to tell apart: a child stacked on a parent that was squash-merged
into the trunk, one whose parent advanced after the child was cut from it, and
one whose parent was rewritten before it merged. Each returns a
``StackFixture`` naming the commits that describe the shape, and
``tests/unit/test_git_repo_helpers.py`` checks each claim by asking the
repository rather than the builder: a builder that quietly produced the wrong
shape would make every later milestone assert against the wrong repository.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git import GitCommandError, Repo

if typ.TYPE_CHECKING:
    from pathlib import Path

# The file the child's own work is committed to, named here because a scenario
# that has to undo that work removes the file by name rather than by the path
# it happens to have been written to.
CHILD_FILE = "child.txt"


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


def commit_file(repo: Repo, path: Path, text: str, message: str) -> str:
    """Write ``text`` to ``path``, commit it, and return the new commit's ID.

    The commit runs through Git rather than through the index object, because
    the stack builders check branches out and merge between commits: an index
    the repository has since rewritten on disk would otherwise be the tree the
    commit was made from.

    Parameters
    ----------
    repo : Repo
        Repository to commit in, on the branch it has checked out.
    path : Path
        File to write; it is created if it is not there.
    text : str
        Contents to write, which is what the commit changes.
    message : str
        Commit message.

    Returns
    -------
    str
        The ID of the commit that wrote the file.

    """
    path.write_text(text, encoding="utf-8")
    repo.git.add(path.as_posix())
    repo.git.commit("-m", message)
    return repo.head.commit.hexsha


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
    commit_file(repo, repo_path / "README.md", "seed", "Seed commit")
    repo.git.branch("-M", branch)
    return repo


def advance(repo: Repo, *, message: str = "Advance") -> str:
    """Commit an empty change on the checked-out branch and return its ID.

    Parameters
    ----------
    repo : Repo
        Repository to commit in.
    message : str, optional
        Commit message.

    Returns
    -------
    str
        The new commit's ID, so a caller can name it as a boundary.

    """
    repo.git.commit("--allow-empty", "-m", message)
    return repo.head.commit.hexsha


def commit_on(repo: Repo, branch: str) -> str:
    """Commit an empty change on ``branch`` and return the new commit's ID.

    The branch is checked out to commit on it and the previous branch is
    checked out again, so the repository is left with the ``HEAD`` it had.

    Parameters
    ----------
    repo : Repo
        Repository to commit in.
    branch : str
        Branch to advance. It must already exist.

    Returns
    -------
    str
        The new commit's ID, so a caller can name it as an observed tip.

    """
    previous = repo.head.ref.name
    repo.git.checkout(branch)
    commit = advance(repo, message=f"Advance {branch}")
    repo.git.checkout(previous)
    return commit


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


@dataclasses.dataclass(frozen=True, slots=True)
class StackFixture:
    """A built repository and the commits one stacking scenario is about.

    ``repo``, ``trunk``, ``parent``, and ``child`` identify the repository and
    the branch names a scenario runs against, with ``trunk`` left checked out.
    The commit fields are the ones boundary recovery names. ``child_tip`` is
    the child's current tip; ``parent_head`` is the parent branch's head;
    ``landed`` is the parent's integration commit on the trunk, which a squash
    merge makes a new commit rather than the parent's head; and ``target`` is
    the commit the child would be replayed onto.

    ``inherited_head`` is the parent's head at the moment the child was cut from
    it — the commit the child actually inherited. It is ``parent_head`` when
    the parent did not move afterwards and an older commit that the parent's
    own history no longer contains when it was rewritten, which is the
    distinction the rewritten shape exists to make observable.

    ``expected_old_base`` is the exclusive replay boundary a working answer
    reports for this shape, and is ``None`` where the boundary is genuinely
    unrecoverable: the child still reaches it, but nothing in the repository
    attests that it is the boundary rather than an ordinary ancestor.
    """

    repo: Repo
    trunk: str
    parent: str
    child: str
    child_tip: str
    parent_head: str
    inherited_head: str
    landed: str
    target: str
    expected_old_base: str | None


def is_ancestor(repo: Repo, ancestor: str, descendant: str) -> bool:
    """Return whether ``ancestor`` is an ancestor of ``descendant``.

    Parameters
    ----------
    repo : Repo
        Repository to ask.
    ancestor : str
        Revision that may be the ancestor.
    descendant : str
        Revision that may be the descendant.

    Returns
    -------
    bool
        Whether Git reports the first commit as an ancestor of the second.

    """
    status, _ = _merge_base(repo, "--is-ancestor", ancestor, descendant)
    return status == 0


def merge_bases(repo: Repo, left: str, right: str) -> tuple[str, ...]:
    """Return every best common ancestor of two commits.

    Parameters
    ----------
    repo : Repo
        Repository to ask.
    left : str
        First revision to compare.
    right : str
        Second revision to compare.

    Returns
    -------
    tuple[str, ...]
        Every commit ``git merge-base --all`` reports, which is more than one
        where the histories crossed.

    """
    _, output = _merge_base(repo, "--all", left, right)
    return tuple(output.split())


def ref_value(repo: Repo, ref: str) -> str | None:
    """Return the commit ``ref`` names, or ``None`` when it does not exist.

    This is the one reader for a ref that may be absent, and it is shared
    because the suites disagree about what absence means: a suite that asks
    about a ref the run may have written wants ``None`` told apart from a
    commit, and a suite that reads a ref that must exist wants the empty string
    its comparisons are written against. The second is a caller's decision, not
    a second reader.

    Parameters
    ----------
    repo : Repo
        Repository the ref is read from.
    ref : str
        Full ref path, or any revision Git resolves.

    Returns
    -------
    str | None
        The commit the ref names, or ``None`` when no such ref exists.

    """
    try:
        return str(repo.git.rev_parse("--verify", "--quiet", ref))
    except GitCommandError:
        return None


def config_section(repo: Repo, branch: str) -> dict[str, str]:
    """Return ``branch``'s configuration section, read from Git directly.

    The section is read through ``git config`` rather than through a store
    under test, because whether a subsection keeps the case and the punctuation
    of the branch name is a fact about Git.

    Parameters
    ----------
    repo : Repo
        Repository the section is read from.
    branch : str
        Branch whose section is wanted.

    Returns
    -------
    dict[str, str]
        Every ``branch.<branch>.*`` setting, keyed by the part after the
        ``branch.<branch>.`` prefix, as Git's own ``--list`` reports them.

    """
    prefix = f"branch.{branch}."
    # ``--list -z`` separates entries with NUL and each entry's key from its
    # value with a newline, so an entry is split on that newline and the whole
    # of the value is kept however many newlines it holds.
    settings: dict[str, str] = {}
    for entry in repo.git.config("--local", "--list", "-z").split("\0"):
        if not entry:
            continue
        key, _, value = entry.partition("\n")
        if key.startswith(prefix):
            settings[key[len(prefix) :]] = value
    return settings


def _merge_base(repo: Repo, *arguments: str) -> tuple[int, str]:
    """Run ``git merge-base`` for its exit status and its output.

    ``--is-ancestor`` answers with its status rather than with its output, and
    a status other than 0 or 1 means the question could not be answered at all
    — a missing object, for one. That is an error rather than a negative
    answer, so it is raised here instead of being read as "no".

    Returns
    -------
    tuple[int, str]
        The exit status and the command's standard output.

    Raises
    ------
    AssertionError
        If Git could not answer the question, so that no caller can read the
        failure as a negative answer.

    """
    status, output, stderr = repo.git.merge_base(
        *arguments,
        with_extended_output=True,
        with_exceptions=False,
    )
    if status not in {0, 1}:
        msg = f"git merge-base {' '.join(arguments)} exited {status}: {stderr.strip()}"
        raise AssertionError(msg)
    return status, output


def squash_merged_stack(
    repo_path: Path,
    *,
    trunk: str = "main",
    parent: str = "parent",
    child: str = "child",
) -> StackFixture:
    """Build a child whose squash-merged parent is still in its history.

    The parent's two commits reach the trunk as one squash commit that names
    the parent's content and not its commits, so the child's history reaches
    the head the child was cut from while the trunk does not. The boundary is
    that head, and ancestry alone still recovers it.

    Parameters
    ----------
    repo_path : Path
        Directory to create the repository in.
    trunk : str, optional
        Branch the squash commit lands on, and the one left checked out.
    parent : str, optional
        Branch whose commits the squash commit carries without naming them.
    child : str, optional
        Branch cut from the parent's head, before the squash merge.

    Returns
    -------
    StackFixture
        The fixture, whose ``inherited_head`` is the parent's head and whose
        ``expected_old_base`` is therefore that same commit.

    """
    repo = seed_repo(repo_path, branch=trunk)
    repo.git.branch(parent, trunk)
    repo.git.checkout(parent)
    commit_file(repo, repo_path / "parent.txt", "parent work", "Parent work")
    parent_head = commit_file(
        repo, repo_path / "parent.txt", "parent work, revised", "More parent work"
    )
    child_tip = _child_work(repo, repo_path, child=child, base=parent_head)
    landed = _squash_merge(repo, parent, trunk=trunk)
    target = advance(repo, message="Advance the trunk after the squash")
    return StackFixture(
        repo=repo,
        trunk=trunk,
        parent=parent,
        child=child,
        child_tip=child_tip,
        parent_head=parent_head,
        inherited_head=parent_head,
        landed=landed,
        target=target,
        expected_old_base=parent_head,
    )


def advanced_parent_stack(
    repo_path: Path,
    *,
    trunk: str = "main",
    parent: str = "parent",
    child: str = "child",
) -> StackFixture:
    """Build a child whose parent committed again after the child was cut.

    The child was cut from the parent's first commit and the parent then
    committed a second, so the parent's head is not an ancestor of the child
    and the boundary is the fork point rather than the head. This is the shape
    a fetched parent pull request describes once the parent has moved on.

    Parameters
    ----------
    repo_path : Path
        Directory to create the repository in.
    trunk : str, optional
        Branch the parent is squash-merged onto, once it has moved on.
    parent : str, optional
        Branch that commits again while the child sits on its first commit.
    child : str, optional
        Branch left where it was cut, which the parent's new head cannot reach.

    Returns
    -------
    StackFixture
        The fixture, whose ``inherited_head`` is the parent's first commit and
        whose ``parent_head`` is absent from the child's history.

    """
    repo = seed_repo(repo_path, branch=trunk)
    repo.git.branch(parent, trunk)
    repo.git.checkout(parent)
    inherited_head = commit_file(
        repo, repo_path / "parent.txt", "parent work", "Parent work"
    )
    child_tip = _child_work(repo, repo_path, child=child, base=inherited_head)
    repo.git.checkout(parent)
    parent_head = commit_file(
        repo, repo_path / "parent.txt", "parent work, revised", "More parent work"
    )
    landed = _squash_merge(repo, parent, trunk=trunk)
    target = advance(repo, message="Advance the trunk after the squash")
    return StackFixture(
        repo=repo,
        trunk=trunk,
        parent=parent,
        child=child,
        child_tip=child_tip,
        parent_head=parent_head,
        inherited_head=inherited_head,
        landed=landed,
        target=target,
        expected_old_base=inherited_head,
    )


def rewritten_parent_stack(
    repo_path: Path,
    *,
    trunk: str = "main",
    parent: str = "parent",
    child: str = "child",
) -> StackFixture:
    """Build a child whose parent branch was rewritten before it merged.

    The parent's commits are rebuilt from the trunk with the same content and
    with new object IDs, which is what a rebase or an amend before the merge
    leaves behind. The child still reaches the head it was cut from, so the
    boundary is a fact about the repository, but nothing attests it: the
    parent's head is now the rewritten commit and the only commit the two heads
    share is the trunk. The rewrite keeps the content, so the squash commit's
    tree still matches the commit whose boundary was lost — content evidence
    that names two commits and therefore establishes neither.

    Parameters
    ----------
    repo_path : Path
        Directory to create the repository in.
    trunk : str, optional
        Name of the trunk branch.
    parent : str, optional
        Name of the parent branch.
    child : str, optional
        Name of the child branch.

    Returns
    -------
    StackFixture
        The repository and the commits the shape is described by, with no
        expected boundary: this shape is the one the command must refuse.

    """
    repo = seed_repo(repo_path, branch=trunk)
    repo.git.branch(parent, trunk)
    repo.git.checkout(parent)
    commit_file(repo, repo_path / "parent.txt", "parent work", "Parent work")
    inherited_head = commit_file(
        repo, repo_path / "parent.txt", "parent work, revised", "More parent work"
    )
    child_tip = _child_work(repo, repo_path, child=child, base=inherited_head)
    parent_head = _rewrite_parent(repo, repo_path, trunk=trunk, parent=parent)
    landed = _squash_merge(repo, parent, trunk=trunk)
    target = advance(repo, message="Advance the trunk after the squash")
    return StackFixture(
        repo=repo,
        trunk=trunk,
        parent=parent,
        child=child,
        child_tip=child_tip,
        parent_head=parent_head,
        inherited_head=inherited_head,
        landed=landed,
        target=target,
        expected_old_base=None,
    )


def _child_work(repo: Repo, repo_path: Path, *, child: str, base: str) -> str:
    """Cut ``child`` from ``base``, commit its work, and leave it checked out."""
    repo.git.branch(child, base)
    repo.git.checkout(child)
    commit_file(repo, repo_path / CHILD_FILE, "child work", "Child work")
    return commit_file(
        repo, repo_path / CHILD_FILE, "child work, revised", "More child work"
    )


def _rewrite_parent(repo: Repo, repo_path: Path, *, trunk: str, parent: str) -> str:
    """Rebuild ``parent`` from ``trunk`` with new IDs and return the new head.

    The commits carry the same content as the ones they replace and different
    messages, so the object IDs change whatever the clock does: a rebuild that
    produced the original commits again would leave the branch un-rewritten.

    Returns
    -------
    str
        The head the rewritten branch now has.

    """
    repo.git.checkout("-B", parent, trunk)
    commit_file(
        repo, repo_path / "parent.txt", "parent work", "Rework the first parent commit"
    )
    return commit_file(
        repo,
        repo_path / "parent.txt",
        "parent work, revised",
        "Rework the second parent commit",
    )


def _squash_merge(repo: Repo, branch: str, *, trunk: str) -> str:
    """Squash ``branch`` onto ``trunk``, commit it, and return the commit's ID.

    ``git merge --squash`` stages the branch's changes without recording a
    merge, so the commit that follows has one parent: it is the shape a forge's
    squash merge leaves on the trunk, and the shape no ancestry relationship
    connects to the commits it carries.

    Returns
    -------
    str
        The commit that carries the branch's changes on the trunk.

    """
    repo.git.checkout(trunk)
    repo.git.merge("--squash", branch)
    repo.git.commit("-m", f"Squash-merge {branch}")
    return repo.head.commit.hexsha
