"""Unit tests for repository layout resolution.

``git donkey`` and ``git plonk`` must agree on two directories: the one Git
commands run from, and the one donkey worktrees live under. These tests pin
both for a repository with a main worktree and for a bare repository whose
checkouts are all linked worktrees, along with the ``donkey.worktreesRoot``
override that replaces either default.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
from git import Repo

from git_donkey import donkey, helpers, layout, plonk
from git_donkey.layout import LayoutKind
from tests.git_repo_helpers import (
    bare_collection,
    corrupt_config,
    repo_with_remote_default,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_USAGE_ERROR_EXIT_CODE = 2

type _CollectionFactory = cabc.Callable[..., tuple[Repo, Path]]


@pytest.fixture
def collection(tmp_path: Path) -> _CollectionFactory:
    """Return a builder for a bare repository with one linked worktree."""

    def build(bare_name: str = "bare.git") -> tuple[Repo, Path]:
        """Clone the seeded remote into a bare collection."""
        _repo, remote = repo_with_remote_default(
            tmp_path / "seed", tmp_path / "remote.git"
        )
        return bare_collection(
            tmp_path / "container",
            Path(remote.git_dir),
            bare_name=bare_name,
        )

    return build


def _resolve(repo: Repo) -> layout.RepositoryLayout:
    """Resolve the layout of ``repo`` from its own worktree listing."""
    stanzas = helpers._parse_worktree_porcelain(repo)
    return layout.resolve_layout(repo, stanzas)


def _raising_layout_error(
    _repo: Repo,
    _stanzas: list[dict[str, object]],
) -> layout.RepositoryLayout:
    """Stand in for the resolver so a boundary test can observe its handling."""
    msg = "layout exploded"
    raise layout.LayoutError(msg)


@pytest.mark.parametrize(
    ("kind", "anchor", "expected"),
    [
        (LayoutKind.MAIN_WORKTREE, "/src/repo", "/src/repo.worktrees"),
        (LayoutKind.MAIN_WORKTREE, "/src/repo.git", "/src/repo.git.worktrees"),
        (LayoutKind.BARE, "/src/repo.git", "/src/repo.worktrees"),
        (LayoutKind.BARE, "/lody/project/bare.git", "/lody/project/bare.worktrees"),
        (LayoutKind.BARE, "/src/project/.bare", "/src/project/.bare.worktrees"),
        (LayoutKind.BARE, "/src/project", "/src/project.worktrees"),
        (LayoutKind.BARE, "/src/.git", "/src/.git.worktrees"),
    ],
)
def test_default_worktrees_root(kind: LayoutKind, anchor: str, expected: str) -> None:
    """The default root is a sibling named after the anchor directory."""
    assert layout.default_worktrees_root(Path(anchor), kind) == Path(expected), (
        "expected the default root to be a '.worktrees' sibling of the anchor, "
        "without a bare repository's '.git' suffix"
    )


@pytest.mark.parametrize(
    ("anchor", "kind", "expected"),
    [
        ("/src/repo", LayoutKind.MAIN_WORKTREE, "/src/repo.worktrees"),
        ("/src/repo.git", LayoutKind.BARE, "/src/repo.worktrees"),
    ],
)
def test_select_layout_uses_the_default_root_when_unset(
    anchor: str,
    kind: LayoutKind,
    expected: str,
) -> None:
    """An unset override selects the default sibling root without Git I/O."""
    selected = layout.select_layout(Path(anchor), kind, None)

    assert selected == layout.RepositoryLayout(
        anchor_dir=Path(anchor),
        kind=kind,
        worktrees_root=Path(expected),
    ), "expected the default root when no override is configured"


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("../trees", "/src/repo/../trees"),
        ("nested/root", "/src/repo/nested/root"),
        ("/opt/trees", "/opt/trees"),
    ],
)
def test_select_layout_honours_the_configured_root(
    configured: str,
    expected: str,
) -> None:
    """A configured root wins and a relative value is joined, not collapsed."""
    selected = layout.select_layout(
        Path("/src/repo"), LayoutKind.MAIN_WORKTREE, configured
    )

    assert selected.worktrees_root == Path(expected), (
        "expected the configured root to replace the default, with any '..' "
        "left for the adapter to resolve"
    )


def test_select_layout_refuses_an_empty_configured_root() -> None:
    """An empty override is a policy error, not a process exit."""
    with pytest.raises(layout.LayoutError) as excinfo:
        layout.select_layout(Path("/src/repo"), LayoutKind.MAIN_WORKTREE, "")

    assert "donkey.worktreesRoot is set but empty" in str(excinfo.value), (
        "expected the policy error to name the empty configuration key"
    )


def test_main_worktree_layout_keeps_the_sibling_root(tmp_path: Path) -> None:
    """A repository with a main worktree keeps its established layout."""
    repo, _remote = repo_with_remote_default(tmp_path / "repo", tmp_path / "remote.git")

    resolved = _resolve(repo)

    assert resolved == layout.RepositoryLayout(
        anchor_dir=(tmp_path / "repo").resolve(),
        kind=LayoutKind.MAIN_WORKTREE,
        worktrees_root=(tmp_path / "repo.worktrees").resolve(),
    ), "expected the main worktree to anchor the layout and its sibling root"


def test_bare_layout_is_resolved_from_a_linked_worktree(
    collection: _CollectionFactory,
    tmp_path: Path,
) -> None:
    """A linked worktree of a bare repository anchors on the bare directory."""
    _bare_repo, session_path = collection()

    resolved = _resolve(Repo(session_path))

    container = (tmp_path / "container").resolve()
    assert resolved == layout.RepositoryLayout(
        anchor_dir=container / "bare.git",
        kind=LayoutKind.BARE,
        worktrees_root=container / "bare.worktrees",
    ), "expected the bare directory to anchor the layout and name its root"


def test_bare_layout_is_resolved_from_the_bare_directory(
    collection: _CollectionFactory,
) -> None:
    """The bare directory resolves to the same layout as its worktrees."""
    bare_repo, session_path = collection()

    assert _resolve(bare_repo) == _resolve(Repo(session_path)), (
        "expected one layout however the repository is entered"
    )


def test_bare_layout_root_stays_clear_of_sibling_worktrees(
    collection: _CollectionFactory,
) -> None:
    """The default root never contains a session manager's own checkouts."""
    _bare_repo, session_path = collection()

    resolved = _resolve(Repo(session_path))

    assert not session_path.resolve().is_relative_to(resolved.worktrees_root), (
        "expected the session worktree to sit outside the donkey worktree root"
    )


@pytest.mark.parametrize(
    ("configured", "expected_from_container"),
    [
        ("../donkeys", "donkeys"),
        ("nested/root", "bare.git/nested/root"),
    ],
)
def test_configured_relative_root_resolves_against_the_anchor(
    collection: _CollectionFactory,
    tmp_path: Path,
    configured: str,
    expected_from_container: str,
) -> None:
    """A relative ``donkey.worktreesRoot`` is relative to the anchor directory."""
    bare_repo, session_path = collection()
    bare_repo.git.config(layout.WORKTREES_ROOT_CONFIG_KEY, configured)

    resolved = _resolve(Repo(session_path))

    expected = (tmp_path / "container" / expected_from_container).resolve()
    assert resolved.worktrees_root == expected, (
        "expected a relative configured root to resolve against the anchor"
    )


def test_configured_absolute_root_replaces_the_default(tmp_path: Path) -> None:
    """An absolute ``donkey.worktreesRoot`` overrides the main-worktree default."""
    repo, _remote = repo_with_remote_default(tmp_path / "repo", tmp_path / "remote.git")
    configured = tmp_path / "elsewhere" / "trees"
    repo.git.config(layout.WORKTREES_ROOT_CONFIG_KEY, configured.as_posix())

    assert _resolve(repo).worktrees_root == configured.resolve(), (
        "expected the configured root to replace the sibling default"
    )


@pytest.mark.parametrize(
    "configure",
    [
        lambda _link: "link/../trees",
        lambda link: (link / ".." / "trees").as_posix(),
    ],
    ids=["relative", "absolute"],
)
def test_configured_root_resolves_a_symlink_before_a_parent_component(
    tmp_path: Path,
    configure: cabc.Callable[[Path], str],
) -> None:
    """A symlink followed by ``..`` keeps its filesystem meaning."""
    repo, _remote = repo_with_remote_default(tmp_path / "repo", tmp_path / "remote.git")
    shared_subdir = tmp_path / "shared" / "subdir"
    shared_subdir.mkdir(parents=True)
    link = tmp_path / "repo" / "link"
    link.symlink_to(shared_subdir, target_is_directory=True)
    repo.git.config(layout.WORKTREES_ROOT_CONFIG_KEY, configure(link))

    resolved = _resolve(repo)

    assert resolved.worktrees_root == (tmp_path / "shared" / "trees").resolve(), (
        "expected the symlink to resolve before '..'; collapsing lexically would "
        "name <repo>/trees and scope cleanup against the wrong directory"
    )


def test_configured_root_expands_the_home_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``~/`` prefix in ``donkey.worktreesRoot`` names the user's home."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", home.as_posix())
    repo, _remote = repo_with_remote_default(tmp_path / "repo", tmp_path / "remote.git")
    repo.git.config(layout.WORKTREES_ROOT_CONFIG_KEY, "~/trees")

    assert _resolve(repo).worktrees_root == (home / "trees").resolve(), (
        "expected Git's path expansion to apply to the configured root"
    )


def test_empty_configured_root_is_refused(tmp_path: Path) -> None:
    """An empty ``donkey.worktreesRoot`` raises a typed configuration error."""
    repo, _remote = repo_with_remote_default(tmp_path / "repo", tmp_path / "remote.git")
    repo.git.config(layout.WORKTREES_ROOT_CONFIG_KEY, "")

    with pytest.raises(layout.LayoutError) as excinfo:
        _resolve(repo)

    assert "donkey.worktreesRoot is set but empty" in str(excinfo.value), (
        "expected the error to name the empty configuration key"
    )


def test_unreadable_configuration_raises_a_layout_error(tmp_path: Path) -> None:
    """A failed configuration read is reported apart from an empty value."""
    repo, _remote = repo_with_remote_default(tmp_path / "repo", tmp_path / "remote.git")
    corrupt_config(repo)
    # Supply the stanza directly: the corrupted file would also break the
    # `git worktree list` call, which must not mask the config-read failure.
    stanzas: list[dict[str, object]] = [{"worktree": (tmp_path / "repo").as_posix()}]

    with pytest.raises(layout.LayoutError) as excinfo:
        layout.resolve_layout(repo, stanzas)

    message = str(excinfo.value)
    assert "could not read donkey.worktreesRoot" in message, (
        "expected an unreadable configuration to be named as such"
    )
    assert "bad config" in message, (
        "expected the Git failure to be preserved in the message"
    )


@pytest.mark.parametrize("stanzas", [[], [{"bare": True}]])
def test_unparsable_worktree_listing_is_refused(
    tmp_path: Path,
    stanzas: list[dict[str, object]],
) -> None:
    """A listing without a leading worktree path raises a typed error."""
    repo, _remote = repo_with_remote_default(tmp_path / "repo", tmp_path / "remote.git")

    with pytest.raises(layout.LayoutError) as excinfo:
        layout.resolve_layout(repo, stanzas)

    assert "could not parse" in str(excinfo.value), (
        "expected the error to name the unparsable listing"
    )


@pytest.mark.parametrize(
    "boundary",
    [donkey._resolve_layout, plonk._resolve_layout],
    ids=["donkey", "plonk"],
)
def test_boundary_exits_on_a_layout_error(
    boundary: cabc.Callable[[Repo, list[dict[str, object]]], layout.RepositoryLayout],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Each command boundary turns a layout error into a usage exit."""
    repo, _remote = repo_with_remote_default(tmp_path / "repo", tmp_path / "remote.git")
    monkeypatch.setattr(layout, "resolve_layout", _raising_layout_error)

    with pytest.raises(SystemExit) as excinfo:
        boundary(repo, [])

    assert excinfo.value.code == _USAGE_ERROR_EXIT_CODE, (
        "expected the boundary to exit with the usage status"
    )
    assert "layout exploded" in capsys.readouterr().err, (
        "expected the boundary to print the policy message"
    )
