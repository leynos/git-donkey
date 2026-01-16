"""Command-line interfaces for the git-donkey tools."""

from __future__ import annotations

import contextlib
import dataclasses
import difflib
import io
import os
import re
import shutil
import subprocess  # noqa: S404
import sys
import typing as typ
from pathlib import Path

import github3
import loctocat
from cyclopts import App
from git import Git, GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo
from github3 import exceptions as github3_exceptions
from plumbum import local
from plumbum.commands.processes import ProcessExecutionError

_GIT_DONKEY_PREFIX = "git-donkey"
_GIT_DONKEY_EMOJI = "💀"
_GIT_TRACK_PREFIX = "git-track"
_GIT_FAFO_PREFIX = "git-fafo"
_GIT_CONFLICT_EMOJI = "⚔️"
_SLUG_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]+$")
_GITHUB_TOKEN_SCOPES = ["user", "repo"]
_GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
_GITHUB_DEVICE_ACCESS_URL = "https://github.com/login/oauth/access_token"
_GIT_DONKEY_CLIENT_ID_ENV = "GIT_DONKEY_GITHUB_CLIENT_ID"
_DEFAULT_GITHUB_CLIENT_ID = "Ov23liD2cKOAh7xmpXKR"


@dataclasses.dataclass(frozen=True, slots=True)
class BaseBranchChoice:
    """Represent the base branch chosen for a new worktree."""

    base_branch: str
    from_saved_cwd_branch: bool


@dataclasses.dataclass(frozen=True, slots=True)
class _DonkeyContext:
    repo_home: Repo
    remote: str
    branch_to_worktree: dict[str, Path]
    worktrees_root: Path


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _stream_or_none(stream: typ.TextIO) -> typ.TextIO | None:
    with contextlib.suppress(AttributeError, io.UnsupportedOperation, ValueError):
        stream.fileno()
        return stream
    return None


def _die(
    prefix: str,
    msg: str,
    code: int = 2,
    *,
    emoji: str | None = None,
) -> typ.NoReturn:
    if emoji is None and prefix == _GIT_DONKEY_PREFIX:
        emoji = _GIT_DONKEY_EMOJI
    if emoji:
        _eprint(f"{prefix}: {emoji} {msg}")
    else:
        _eprint(f"{prefix}: {msg}")
    raise SystemExit(code)


def _die_conflict(prefix: str, msg: str, code: int = 2) -> typ.NoReturn:
    _die(prefix, msg, code, emoji=_GIT_CONFLICT_EMOJI)


def _find_repo(prefix: str) -> Repo:
    try:
        return Repo(Path.cwd(), search_parent_directories=True)
    except (InvalidGitRepositoryError, NoSuchPathError):
        _die(prefix, "not inside a Git repository", 2)


def _get_checked_out_branch_name(repo: Repo, prefix: str) -> str:
    if repo.head.is_detached:
        _die(
            prefix,
            "HEAD is detached in the current directory; checkout a branch first",
            2,
        )
    return repo.active_branch.name


def _parse_worktree_porcelain(repo: Repo) -> list[dict[str, object]]:
    """Parse `git worktree list --porcelain` output into stanzas."""
    try:
        out = repo.git.worktree("list", "--porcelain", "-z")
        parts = out.split("\0")
    except GitCommandError:
        out = repo.git.worktree("list", "--porcelain")
        parts = out.splitlines()

    stanzas: list[dict[str, object]] = []
    current: dict[str, object] = {}
    for part in parts:
        if part == "":
            if current:
                stanzas.append(current)
                current = {}
            continue
        if " " in part:
            key, value = part.split(" ", 1)
            current[key] = value
        else:
            current[part] = True
    if current:
        stanzas.append(current)
    return stanzas


def _main_worktree_path_from_list(
    stanzas: list[dict[str, object]],
    prefix: str,
) -> Path:
    if not stanzas or "worktree" not in stanzas[0]:
        _die(prefix, "could not parse `git worktree list --porcelain` output", 2)

    if stanzas[0].get("bare", False):
        _die(
            prefix,
            "repository appears to be bare; this command expects a non-bare repo "
            "with a main worktree",
            2,
        )

    return Path(str(stanzas[0]["worktree"])).expanduser().resolve()


def _branch_to_worktree_map(stanzas: list[dict[str, object]]) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for stanza in stanzas:
        worktree = stanza.get("worktree")
        branch = stanza.get("branch")
        if not (worktree and branch):
            continue
        branch_name = str(branch)
        prefix = "refs/heads/"
        if branch_name.startswith(prefix):
            out[branch_name[len(prefix) :]] = Path(str(worktree)).expanduser().resolve()
    return out


def _first_remote_name(repo: Repo, prefix: str) -> str:
    names = [line.strip() for line in repo.git.remote().splitlines() if line.strip()]
    if not names:
        _die(prefix, "no remotes configured", 1)
    return names[0]


def _fetch_remote(repo: Repo, remote: str, prefix: str) -> None:
    _eprint(f"Fetching: {remote}")
    try:
        repo.git.fetch("--prune", remote)
    except GitCommandError as exc:
        _die(prefix, f"fetch failed: {exc}", 1)


def _ref_exists(repo: Repo, ref: str) -> bool:
    try:
        repo.git.show_ref("--verify", "--quiet", ref)
    except GitCommandError:
        return False
    return True


def _local_branch_exists(repo: Repo, branch: str) -> bool:
    return _ref_exists(repo, f"refs/heads/{branch}")


def _remote_branch_exists(repo: Repo, remote: str, branch: str) -> bool:
    return _ref_exists(repo, f"refs/remotes/{remote}/{branch}")


def _ensure_local_tracking_branch(
    repo: Repo,
    remote: str,
    branch: str,
    prefix: str,
) -> None:
    if _local_branch_exists(repo, branch):
        return
    if not _remote_branch_exists(repo, remote, branch):
        _die(
            prefix,
            f"branch '{branch}' does not exist locally, and '{remote}/{branch}' "
            "does not exist on the remote",
            1,
        )
    repo.git.branch("--track", branch, f"{remote}/{branch}")


def _ensure_upstream_for_branch(
    repo: Repo,
    *,
    branch: str,
    remote: str,
    prefix: str,
) -> None:
    try:
        local_branch = repo.heads[branch]
    except IndexError:
        return

    desired = f"{remote}/{branch}"
    tracking = local_branch.tracking_branch()
    if tracking is not None and str(tracking) == desired:
        return
    if not _remote_branch_exists(repo, remote, branch):
        return
    try:
        repo.git.branch("--set-upstream-to", desired, branch)
    except GitCommandError as exc:
        _die(
            prefix,
            f"could not set upstream for '{branch}' to {desired}: {exc}",
            1,
        )


def _ahead_behind(repo: Repo, left: str, right: str) -> tuple[int, int]:
    out = repo.git.rev_list("--left-right", "--count", f"{left}...{right}").strip()
    ahead, behind = out.split()
    return int(ahead), int(behind)


def _prompt_yes_no(question: str, *, default: bool = False) -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        _eprint("Non-interactive stdin/stdout; skipping prompt (treating as 'no').")
        return False

    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        response = input(question + suffix).strip().lower()
        if not response:
            return default
        if response in {"y", "yes"}:
            return True
        if response in {"n", "no"}:
            return False
        _eprint("Please answer y or n.")


def choose_base_branch(
    saved_cwd_branch: str,
    origin_arg: str | None,
) -> BaseBranchChoice:
    """Choose the base branch for a new worktree.

    Parameters
    ----------
    saved_cwd_branch : str
        The branch checked out in the current working directory.
    origin_arg : str | None
        The origin argument provided by the user.

    Returns
    -------
    BaseBranchChoice
        The resolved base branch and whether it came from the CWD.

    """
    if origin_arg is None:
        return BaseBranchChoice(base_branch="main", from_saved_cwd_branch=False)
    if origin_arg == ".":
        return BaseBranchChoice(
            base_branch=saved_cwd_branch,
            from_saved_cwd_branch=True,
        )
    return BaseBranchChoice(base_branch=origin_arg, from_saved_cwd_branch=False)


def _pull_rebase_in_worktree(worktree_dir: Path, remote: str, branch: str) -> None:
    git = Git(str(worktree_dir))
    git.pull("--rebase", remote, branch)


def _base_branch_behind_count(
    context: _DonkeyContext,
    *,
    base_branch: str,
    prefix: str,
) -> int:
    _ensure_local_tracking_branch(
        context.repo_home,
        context.remote,
        base_branch,
        prefix,
    )

    if not _remote_branch_exists(context.repo_home, context.remote, base_branch):
        _die(
            prefix,
            f"remote counterpart '{context.remote}/{base_branch}' not found",
            1,
        )

    _ahead, behind = _ahead_behind(
        context.repo_home,
        base_branch,
        f"{context.remote}/{base_branch}",
    )
    return behind


def _update_base_branch_in_worktree(
    context: _DonkeyContext,
    *,
    base_branch: str,
    prefix: str,
) -> None:
    worktree = context.branch_to_worktree.get(base_branch)
    if worktree is not None:
        _eprint(f"Updating '{base_branch}' in worktree: {worktree}")
        try:
            _pull_rebase_in_worktree(worktree, context.remote, base_branch)
        except GitCommandError as exc:
            _die(prefix, f"pull --rebase failed in {worktree}: {exc}", 1)
        return

    tmp_root = context.worktrees_root / ".donkey-tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = (tmp_root / f"update-{base_branch}-{os.getpid()}").resolve()

    _eprint(
        f"No worktree has '{base_branch}' checked out; using temporary worktree: "
        f"{tmp_path}"
    )
    try:
        context.repo_home.git.worktree("add", str(tmp_path), base_branch)
        _pull_rebase_in_worktree(tmp_path, context.remote, base_branch)
    except GitCommandError as exc:
        _die(prefix, f"failed to update base branch via temporary worktree: {exc}", 1)
    finally:
        try:
            context.repo_home.git.worktree("remove", str(tmp_path))
        except GitCommandError:
            try:
                context.repo_home.git.worktree("remove", "-f", str(tmp_path))
            except GitCommandError:
                _eprint(
                    f"Warning: could not remove temporary worktree at {tmp_path}; "
                    "remove it manually."
                )


def _maybe_update_base_branch(
    context: _DonkeyContext,
    *,
    base_branch: str,
    no_pull: bool,
    prefix: str,
) -> None:
    if no_pull:
        return

    behind = _base_branch_behind_count(
        context,
        base_branch=base_branch,
        prefix=prefix,
    )
    if behind <= 0:
        return

    if not _prompt_yes_no(
        f"Base branch '{base_branch}' is behind "
        f"'{context.remote}/{base_branch}' by {behind} commit(s). Pull --rebase "
        "it first?"
    ):
        return

    _update_base_branch_in_worktree(
        context,
        base_branch=base_branch,
        prefix=prefix,
    )


def _worktrees_root(home_dir: Path) -> Path:
    return (home_dir.parent / f"{home_dir.name}.worktrees").resolve()


def _create_worktree(
    context: _DonkeyContext,
    *,
    branch_name: str,
    base_branch: str,
    target_path: Path,
) -> None:
    prefix = _GIT_DONKEY_PREFIX
    existing_worktree = context.branch_to_worktree.get(branch_name)
    if existing_worktree is not None:
        _die_conflict(
            prefix,
            f"branch '{branch_name}' is already checked out at: {existing_worktree}",
            1,
        )

    if target_path.exists():
        _die_conflict(prefix, f"target path already exists: {target_path}", 1)

    if _local_branch_exists(context.repo_home, branch_name):
        _eprint(f"Creating worktree for existing local branch '{branch_name}'")
        _ensure_upstream_for_branch(
            context.repo_home,
            branch=branch_name,
            remote=context.remote,
            prefix=prefix,
        )
        try:
            context.repo_home.git.worktree("add", str(target_path), branch_name)
        except GitCommandError as exc:
            _die(prefix, f"worktree add failed: {exc}", 1)
        return

    if _remote_branch_exists(context.repo_home, context.remote, branch_name):
        _eprint(
            f"Branch '{branch_name}' exists on {context.remote}; creating a "
            "local tracking branch"
        )
        try:
            _ensure_local_tracking_branch(
                context.repo_home,
                context.remote,
                branch_name,
                prefix,
            )
            context.repo_home.git.worktree("add", str(target_path), branch_name)
        except GitCommandError as exc:
            _die(prefix, f"worktree add failed: {exc}", 1)
        return

    _eprint(
        f"Creating new branch '{branch_name}' from '{base_branch}' in a new worktree"
    )
    try:
        _ensure_local_tracking_branch(
            context.repo_home,
            context.remote,
            base_branch,
            prefix,
        )
        context.repo_home.git.worktree(
            "add",
            "-b",
            branch_name,
            str(target_path),
            base_branch,
        )
    except GitCommandError as exc:
        _die(prefix, f"worktree add failed: {exc}", 1)


def _load_donkey_context() -> tuple[_DonkeyContext, str]:
    repo_cwd = _find_repo(_GIT_DONKEY_PREFIX)
    saved_cwd_branch = _get_checked_out_branch_name(repo_cwd, _GIT_DONKEY_PREFIX)

    stanzas = _parse_worktree_porcelain(repo_cwd)
    home_dir = _main_worktree_path_from_list(stanzas, _GIT_DONKEY_PREFIX)
    branch_to_worktree = _branch_to_worktree_map(stanzas)

    os.chdir(home_dir)
    repo_home = Repo(home_dir)

    remote = _first_remote_name(repo_home, _GIT_DONKEY_PREFIX)
    _eprint(f"Using remote: {remote}")
    _fetch_remote(repo_home, remote, _GIT_DONKEY_PREFIX)

    worktrees_root = _worktrees_root(home_dir)
    context = _DonkeyContext(
        repo_home=repo_home,
        remote=remote,
        branch_to_worktree=branch_to_worktree,
        worktrees_root=worktrees_root,
    )
    return context, saved_cwd_branch


def run_git_donkey(
    branch_name: str,
    origin_branch: str | None = None,
    *,
    no_pull: bool = False,
) -> int:
    """Run the git-donkey workflow.

    Parameters
    ----------
    branch_name : str
        Branch name for the new worktree.
    origin_branch : str | None
        Base branch (default: main). Use '.' for the CWD branch.
    no_pull : bool
        If True, do not prompt to pull --rebase the base branch.

    Returns
    -------
    int
        The desired process exit code.

    """
    context, saved_cwd_branch = _load_donkey_context()
    base_choice = choose_base_branch(saved_cwd_branch, origin_branch)
    base_branch = base_choice.base_branch

    _maybe_update_base_branch(
        context,
        base_branch=base_branch,
        no_pull=no_pull,
        prefix=_GIT_DONKEY_PREFIX,
    )

    context.worktrees_root.mkdir(parents=True, exist_ok=True)
    target_path = (context.worktrees_root / branch_name).resolve()

    _create_worktree(
        context,
        branch_name=branch_name,
        base_branch=base_branch,
        target_path=target_path,
    )

    print(f"🫏 Worktree created: {target_path}")
    return 0


def _suggest_remote_branches(repo: Repo, remote: str, branch: str) -> list[str]:
    available = sorted({
        ref.remote_head for ref in repo.remote(remote).refs if ref.remote_head != "HEAD"
    })
    return difflib.get_close_matches(branch, available, n=8, cutoff=0.35)


def run_git_track(branch: str) -> int:
    """Run the git-track workflow.

    Parameters
    ----------
    branch : str
        Branch name (for example, feature/foo).

    Returns
    -------
    int
        The desired process exit code.

    """
    repo = _find_repo(_GIT_TRACK_PREFIX)
    remote = _first_remote_name(repo, _GIT_TRACK_PREFIX)

    _eprint(f"Using remote: {remote}")
    _fetch_remote(repo, remote, _GIT_TRACK_PREFIX)

    head = None
    try:
        head = repo.heads[branch]
        _eprint(f"Local branch exists: {branch}")
        _eprint(f"Switching to: {branch}")
        head.checkout()
    except IndexError:
        head = None
    except GitCommandError as exc:
        _die(
            _GIT_TRACK_PREFIX,
            f"could not switch to local branch '{branch}': {exc}",
            1,
        )

    if not _remote_branch_exists(repo, remote, branch):
        suggestions = _suggest_remote_branches(repo, remote, branch)
        hint = ""
        if suggestions:
            hint = "\nDid you mean:\n  " + "\n  ".join(suggestions)
        _die(_GIT_TRACK_PREFIX, f"remote branch not found: {remote}/{branch}{hint}", 1)

    if head is not None:
        _eprint(f"Updating from: {remote}/{branch}")
        try:
            repo.git.merge(f"{remote}/{branch}")
        except GitCommandError as exc:
            _die(_GIT_TRACK_PREFIX, f"update failed (merge): {exc}", 1)
        return 0

    _eprint(f"Creating tracking branch from: {remote}/{branch}")
    try:
        repo.git.checkout("-t", f"{remote}/{branch}")
    except GitCommandError as exc:
        _die(
            _GIT_TRACK_PREFIX,
            f"could not create tracking branch '{branch}' from {remote}/{branch}: "
            f"{exc}",
            1,
        )

    return 0


def validate_slug(value: str, *, label: str, prefix: str) -> str:
    """Validate slug-like inputs for git-fafo arguments.

    Parameters
    ----------
    value : str
        The input value to validate.
    label : str
        A human-friendly label for error messages.
    prefix : str
        The CLI name used for error messages.

    Returns
    -------
    str
        The validated input value.

    """
    if _SLUG_PATTERN.fullmatch(value):
        return value

    _eprint(f"{prefix}: invalid {label}: {value}")
    _eprint("  (allowed characters: a-z A-Z 0-9 _ - .)")
    raise SystemExit(1)


def _require_command(name: str, prefix: str) -> None:
    if shutil.which(name) is None:
        _die(prefix, f"required command not found: {name}", 1)


def _credentials_path() -> Path:
    configured = os.environ.get("GIT_DONKEY_CREDENTIALS_FILE")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".config" / "git-donkey" / "github-token"


def _read_token_from_file(path: Path) -> str | None:
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return None
    if not lines:
        return None
    token = lines[0].strip()
    return token or None


def _write_token_file(path: Path, token: str, auth_id: int | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = f"{token}\n"
    if auth_id is not None:
        payload += f"{auth_id}\n"
    path.write_text(payload)
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def _ensure_interactive() -> None:
    if sys.stdin.isatty() and sys.stdout.isatty():
        return
    _die(
        _GIT_FAFO_PREFIX,
        "missing GitHub token and no interactive terminal available; set "
        "GITHUB_TOKEN, GH_TOKEN, or GIT_DONKEY_CREDENTIALS_FILE",
        1,
    )


def _device_flow_token(credentials_path: Path) -> str:
    _ensure_interactive()
    client_id = os.environ.get(_GIT_DONKEY_CLIENT_ID_ENV) or _DEFAULT_GITHUB_CLIENT_ID

    authenticator = loctocat.Authenticator(
        client_id=client_id,
        auth_url=_GITHUB_DEVICE_CODE_URL,
        token_url=_GITHUB_DEVICE_ACCESS_URL,
        scopes=_GITHUB_TOKEN_SCOPES,
    )
    auth_info = authenticator.ping()
    _eprint("Complete device authorisation to continue:")
    _eprint(f"  URL: {auth_info.verification_uri}")
    _eprint(f"  Code: {auth_info.user_code}")

    token = authenticator.poll()
    if not token:
        _die(_GIT_FAFO_PREFIX, "failed to create GitHub device token", 1)

    _write_token_file(credentials_path, token, None)
    return token


def _github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token

    credentials_path = _credentials_path()
    stored_token = _read_token_from_file(credentials_path)
    if stored_token:
        return stored_token

    return _device_flow_token(credentials_path)


def _create_remote_repository(*, token: str, repo_name: str) -> str:
    github = github3.login(token=token)
    if github is None:
        _die(_GIT_FAFO_PREFIX, "GitHub authentication failed", 1)

    user = github.me()
    if user is None or not getattr(user, "login", None):
        _die(_GIT_FAFO_PREFIX, "could not determine GitHub username", 1)

    try:
        github.create_repository(repo_name, private=False)
    except github3_exceptions.UnprocessableEntity as exc:
        if _is_repo_already_exists_error(exc):
            _die_conflict(
                _GIT_FAFO_PREFIX,
                f"GitHub repository '{user.login}/{repo_name}' already exists. "
                "Pick a new name or delete the existing repository before "
                "running git-fafo again.",
                1,
            )
        _die(
            _GIT_FAFO_PREFIX,
            f"GitHub repository creation failed: {exc}",
            1,
        )
    return str(user.login)


def _is_repo_already_exists_error(
    error: github3_exceptions.GitHubError,
) -> bool:
    message = str(getattr(error, "message", "") or "").lower()
    if "already exists" in message:
        return True

    for detail in getattr(error, "errors", []):
        if isinstance(detail, dict):
            detail_message = str(detail.get("message", "")).lower()
            detail_code = str(detail.get("code", "")).lower()
            if "already exists" in detail_message or detail_code == "already_exists":
                return True
        elif "already exists" in str(detail).lower():
            return True

    return False


def _run_copier_interactive(*, template: str, repo_name: str) -> None:
    env = os.environ.copy()
    copier_path = shutil.which("copier", path=env.get("PATH")) or "copier"
    cmd = [copier_path, "copy", template, repo_name]
    stdin = _stream_or_none(sys.stdin)
    stdout = _stream_or_none(sys.stdout)
    stderr = _stream_or_none(sys.stderr)
    try:
        subprocess.run(  # noqa: S603
            cmd,
            check=True,
            env=env,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
        )
    except subprocess.CalledProcessError as exc:
        raise ProcessExecutionError(cmd, exc.returncode, "", "") from exc


def _run_fafo_commands(*, token: str, repo_name: str, language: str) -> None:
    owner = _create_remote_repository(token=token, repo_name=repo_name)
    template = f"git@github.com:{owner}/agent-template-{language}"
    repo_path = Path(repo_name)

    try:
        env_overrides = {}
        stub_log = os.environ.get("STUB_LOG")
        if stub_log is not None:
            env_overrides["STUB_LOG"] = stub_log
        env_overrides["PATH"] = os.environ.get("PATH", "")
        with local.env(**env_overrides):
            git = local["git"]
            _run_copier_interactive(template=template, repo_name=repo_name)
            with local.cwd(repo_path):
                git["init"]()
                git["remote", "add", "origin", f"git@github.com:{owner}/{repo_name}"]()
                git["branch", "-m", "main"]()
                git["commit", "-m", "Initial commit", "--allow-empty"]()
                git["add", "."]()
                if git["status", "--porcelain"]().strip():
                    git["commit", "-m", "Add repo skeleton"]()
                git["push", "--set-upstream", "origin", "main"]()
    except ProcessExecutionError as exc:
        _die(_GIT_FAFO_PREFIX, f"command failed: {exc}", 1)


def run_git_fafo(repo_name: str, language: str) -> int:
    """Run the git-fafo workflow.

    Parameters
    ----------
    repo_name : str
        Name for the new repository (alphanumeric, _, -, . only).
    language : str
        Programming language for the project (alphanumeric, _, -, . only).

    Returns
    -------
    int
        The desired process exit code.

    """
    _require_command("git", _GIT_FAFO_PREFIX)
    _require_command("copier", _GIT_FAFO_PREFIX)

    repo_name = validate_slug(repo_name, label="repo name", prefix=_GIT_FAFO_PREFIX)
    language = validate_slug(language, label="language", prefix=_GIT_FAFO_PREFIX)

    if Path(repo_name).exists():
        _eprint("You did that one already!")
        return 1

    token = _github_token()
    _run_fafo_commands(token=token, repo_name=repo_name, language=language)
    return 0


_donkey_app = App(
    name="git donkey",
    help=(
        "Create a linked worktree at ../<repo>.worktrees/<branch>, branching from "
        "main (default), a specified origin branch, or '.' meaning the branch "
        "currently checked out in the CWD."
    ),
)


@_donkey_app.default
def _donkey_cli(
    branch_name: str,
    origin_branch: str | None = None,
    *,
    no_pull: bool = False,
) -> None:
    """CLI wrapper for git-donkey."""
    raise SystemExit(
        run_git_donkey(
            branch_name,
            origin_branch,
            no_pull=no_pull,
        )
    )


_track_app = App(
    name="git track",
    help=(
        "Fetch from the first remote, then switch to an existing local branch "
        "and update it, or create a new tracking branch from <remote>/<branch>."
    ),
)


@_track_app.default
def _track_cli(branch: str) -> None:
    """CLI wrapper for git-track."""
    raise SystemExit(run_git_track(branch))


_fafo_app = App(
    name="git fafo",
    help="Quickly scaffold and publish a new GitHub repository.",
)


@_fafo_app.default
def _fafo_cli(repo_name: str, language: str) -> None:
    """CLI wrapper for git-fafo."""
    raise SystemExit(run_git_fafo(repo_name, language))


def git_donkey() -> None:
    """Console entrypoint for git-donkey."""
    _donkey_app()


def git_track() -> None:
    """Console entrypoint for git-track."""
    _track_app()


def git_fafo() -> None:
    """Console entrypoint for git-fafo."""
    _fafo_app()
