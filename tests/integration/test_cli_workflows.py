"""Integration coverage for the git-donkey CLI commands."""

from __future__ import annotations

import dataclasses
import os
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    import pytest
from git import Repo

from git_donkey import cli


def _configure_repo(repo: Repo) -> None:
    config = repo.config_writer()
    config.set_value("user", "name", "Test User")
    config.set_value("user", "email", "test@example.com")
    config.release()


def _seed_repo(repo: Repo, filename: str, content: str) -> None:
    path = Path(repo.working_tree_dir or ".") / filename
    path.write_text(content)
    repo.index.add([str(path)])
    repo.index.commit("Seed commit")


def _setup_repo(tmp_path: Path) -> tuple[Path, Path]:
    remote_path = tmp_path / "remote.git"
    local_path = tmp_path / "local"

    Repo.init(remote_path, bare=True)
    local_repo = Repo.init(local_path)
    _configure_repo(local_repo)
    local_repo.create_remote("origin", remote_path.as_posix())

    _seed_repo(local_repo, "README.md", "seed")
    local_repo.git.branch("-M", "main")
    local_repo.remote("origin").push("main")

    return local_path, remote_path


def test_git_track_creates_tracking_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git-track should create and check out a tracking branch."""
    local_path, _remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)

    repo.git.checkout("-b", "feature/track")
    _seed_repo(repo, "feature.txt", "feature")
    repo.remote("origin").push("feature/track")
    repo.git.checkout("main")
    repo.delete_head("feature/track", force=True)

    monkeypatch.chdir(local_path)
    exit_code = cli.run_git_track("feature/track")

    assert exit_code == 0
    assert Repo(local_path).active_branch.name == "feature/track"


def test_git_donkey_creates_new_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git-donkey should create a new linked worktree on a new branch."""
    local_path, _remote_path = _setup_repo(tmp_path)

    monkeypatch.chdir(local_path)
    exit_code = cli.run_git_donkey("feature/worktree", no_pull=True)

    assert exit_code == 0

    worktree_root = local_path.parent / f"{local_path.name}.worktrees"
    worktree_path = worktree_root / "feature/worktree"
    assert worktree_path.exists()
    assert Repo(worktree_path).active_branch.name == "feature/worktree"


def test_git_donkey_sets_upstream_for_existing_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git-donkey should keep local branches and set upstream if remote exists."""
    local_path, _remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)

    repo.git.checkout("-b", "feature/existing")
    _seed_repo(repo, "feature.txt", "feature")
    repo.remote("origin").push("feature/existing")
    repo.git.checkout("main")

    monkeypatch.chdir(local_path)
    exit_code = cli.run_git_donkey("feature/existing", no_pull=True)

    assert exit_code == 0

    worktree_root = local_path.parent / f"{local_path.name}.worktrees"
    worktree_path = worktree_root / "feature/existing"
    assert worktree_path.exists()
    worktree_repo = Repo(worktree_path)
    assert worktree_repo.active_branch.name == "feature/existing"
    assert (
        str(worktree_repo.active_branch.tracking_branch()) == "origin/feature/existing"
    )


def test_git_fafo_runs_expected_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git-fafo should invoke copier, GitHub API, and git with expected arguments."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "calls.log"

    stub_template = (
        "#!/usr/bin/env python3\n"
        "from __future__ import annotations\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "log_path = Path(os.environ['STUB_LOG'])\n"
        "name = Path(sys.argv[0]).name\n"
        "with log_path.open('a') as handle:\n"
        "    handle.write(f\"{name} {' '.join(sys.argv[1:])}\\n\")\n"
        "if name == 'copier':\n"
        "    Path(sys.argv[-1]).mkdir(parents=True, exist_ok=True)\n"
        "sys.exit(0)\n"
    )

    for cmd in ("copier", "git"):
        stub_path = bin_dir / cmd
        stub_path.write_text(stub_template)
        stub_path.chmod(0o755)

    created: dict[str, str | bool] = {}

    @dataclasses.dataclass
    class _StubUser:
        login: str

    @dataclasses.dataclass
    class _StubGitHub:
        login: str
        created: dict[str, str | bool]

        def me(self) -> _StubUser:
            return _StubUser(self.login)

        def create_repository(self, name: str, *, private: bool = False) -> None:
            self.created["name"] = name
            self.created["private"] = private

    def _fake_login(*, token: str | None = None) -> _StubGitHub:
        created["token"] = token or ""
        return _StubGitHub("example", created)

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("USER", "example")
    monkeypatch.setenv("STUB_LOG", str(log_path))
    auth_value = "fake-token"
    monkeypatch.setenv("GITHUB_TOKEN", auth_value)
    monkeypatch.setattr(cli.github3, "login", _fake_login)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.run_git_fafo("demo-repo", "python")

    assert exit_code == 0
    assert (tmp_path / "demo-repo").exists()

    calls = log_path.read_text().splitlines()
    assert (
        calls[0] == "copier copy git@github.com:example/agent-template-python demo-repo"
    )
    assert "git init" in calls[1]
    assert created["token"] == auth_value
    assert created["name"] == "demo-repo"
    assert created["private"] is False
