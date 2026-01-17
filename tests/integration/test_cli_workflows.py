"""Integration coverage for git-donkey command workflows.

Exercises git-donkey worktree creation, git-track branch synchronization, and
git-fafo repository scaffolding in real repositories.

Run with `make test` or `pytest tests/integration/test_cli_workflows.py`.
"""

from __future__ import annotations

import os
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    import pytest
from git import Repo

from git_donkey import donkey, fafo, track


def _configure_repo(repo: Repo) -> None:
    """Configure user identity for commit creation."""
    config = repo.config_writer()
    config.set_value("user", "name", "Test User")
    config.set_value("user", "email", "test@example.com")
    config.release()


def _seed_repo(repo: Repo, filename: str, content: str) -> None:
    """Create and commit a file in the repository."""
    path = Path(repo.working_tree_dir or ".") / filename
    path.write_text(content)
    repo.index.add([str(path)])
    repo.index.commit("Seed commit")


def _setup_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Create a local repo with a bare remote and a seeded main branch."""
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
    exit_code = track.run_git_track("feature/track")

    assert exit_code == 0, "expected git-track to exit successfully"
    assert Repo(local_path).active_branch.name == "feature/track", (
        "expected feature/track to be checked out"
    )


def test_git_donkey_creates_new_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git-donkey should create a new linked worktree on a new branch."""
    local_path, _remote_path = _setup_repo(tmp_path)

    monkeypatch.chdir(local_path)
    exit_code = donkey.run_git_donkey("feature/worktree", no_pull=True)

    assert exit_code == 0, "expected git-donkey to exit successfully"

    worktree_root = local_path.parent / f"{local_path.name}.worktrees"
    worktree_path = worktree_root / "feature/worktree"
    assert worktree_path.exists(), "expected worktree directory to be created"
    assert Repo(worktree_path).active_branch.name == "feature/worktree", (
        "expected worktree branch name to match"
    )


def test_git_donkey_allows_local_only_base_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git-donkey should allow local-only base branches without --no-pull."""
    local_path, _remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)

    repo.git.checkout("-b", "feature/local-only")
    _seed_repo(repo, "local.txt", "local change")

    monkeypatch.chdir(local_path)
    exit_code = donkey.run_git_donkey("feature/from-local", ".", no_pull=False)

    assert exit_code == 0, "expected git-donkey to exit successfully"

    worktree_root = local_path.parent / f"{local_path.name}.worktrees"
    worktree_path = worktree_root / "feature/from-local"
    assert worktree_path.exists(), "expected worktree directory to be created"
    assert Repo(worktree_path).active_branch.name == "feature/from-local", (
        "expected worktree branch name to match"
    )


def test_git_donkey_updates_base_branch_when_behind_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git-donkey should update a behind base branch when the prompt is accepted."""
    local_path, _remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)

    repo.git.checkout("main")
    _seed_repo(repo, "upstream.txt", "upstream change")
    repo.remote("origin").push("main")
    repo.git.reset("--hard", "HEAD~1")

    monkeypatch.chdir(local_path)
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", lambda *_: True)

    called: dict[str, object] = {}

    def _fake_update_base_branch_in_worktree(
        context: donkey._DonkeyContext,
        *,
        base_branch: str,
        prefix: str,
    ) -> None:
        called["context"] = context
        called["base_branch"] = base_branch
        called["prefix"] = prefix

    monkeypatch.setattr(
        donkey,
        "_update_base_branch_in_worktree",
        _fake_update_base_branch_in_worktree,
    )

    exit_code = donkey.run_git_donkey("feature/update", no_pull=False)

    assert exit_code == 0, "expected git-donkey to exit successfully"
    assert called["base_branch"] == "main", "expected main to be updated"
    assert called["prefix"] == donkey._GIT_DONKEY_PREFIX, (
        "expected git-donkey prefix in update call"
    )


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
    exit_code = donkey.run_git_donkey("feature/existing", no_pull=True)

    assert exit_code == 0, "expected git-donkey to exit successfully"

    worktree_root = local_path.parent / f"{local_path.name}.worktrees"
    worktree_path = worktree_root / "feature/existing"
    assert worktree_path.exists(), "expected worktree directory to be created"
    worktree_repo = Repo(worktree_path)
    assert worktree_repo.active_branch.name == "feature/existing", (
        "expected existing branch to be checked out in worktree"
    )
    assert (
        str(worktree_repo.active_branch.tracking_branch()) == "origin/feature/existing"
    ), "expected worktree branch to track origin/feature/existing"


def test_git_fafo_existing_repo_early_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """git-fafo should exit early if the target repo directory already exists."""
    existing_repo_path = tmp_path / "demo-repo"
    existing_repo_path.mkdir()

    monkeypatch.chdir(tmp_path)

    called: dict[str, bool] = {}

    def _fake_run_fafo_commands(*_: object, **__: object) -> None:
        called["ran"] = True

    monkeypatch.setattr(fafo.helpers, "_require_command", lambda *_: None)
    monkeypatch.setattr(fafo, "_run_fafo_commands", _fake_run_fafo_commands)

    exit_code = fafo.run_git_fafo("demo-repo", "python")

    assert exit_code == 1, "expected git-fafo to return error for existing path"
    assert called == {}, "expected no git-fafo commands to run"
    assert "You did that one already!" in capsys.readouterr().err, (
        "expected early-return message on stderr"
    )


def test_git_fafo_runs_expected_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    github_stubs: tuple[type[typ.Any], type[typ.Any]],
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

    stub_github = github_stubs[1]

    def _fake_login(*, token: str | None = None) -> object:
        created["token"] = token or ""
        return stub_github("example", created)

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("USER", "example")
    monkeypatch.setenv("STUB_LOG", str(log_path))
    auth_value = "fake-token"
    monkeypatch.setenv("GITHUB_TOKEN", auth_value)
    monkeypatch.setattr(fafo.github3, "login", _fake_login)
    monkeypatch.chdir(tmp_path)

    exit_code = fafo.run_git_fafo("demo-repo", "python")

    assert exit_code == 0, "expected git-fafo to exit successfully"
    assert (tmp_path / "demo-repo").exists(), "expected repo directory to exist"

    calls = log_path.read_text().splitlines()
    assert (
        calls[0] == "copier copy git@github.com:example/agent-template-python demo-repo"
    ), "expected copier to run with the template and repo name"
    assert "git init" in calls[1], "expected git init to be invoked"
    assert created["token"] == auth_value, "expected token passed to github3 login"
    assert created["name"] == "demo-repo", "expected repo to be created"
    assert created["private"] is False, "expected repo to be public"
