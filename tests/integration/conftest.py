"""Shared helpers and fixtures for git-donkey integration tests.

The integration suite exercises command-line workflows that use GitPython,
plumbum, and stubbed external binaries. This module centralizes repository
configuration helpers and reusable stub command setup so workflow tests can
focus on their scenario-specific assertions. It also holds the pytest-bdd
``then``/``when`` steps shared verbatim by ``test_git_donkey_bases_bdd.py``
and ``test_git_donkey_reuse_bdd.py``, since pytest-bdd discovers step
definitions placed in a conftest module.
"""

from __future__ import annotations

import dataclasses
import json
import os
import typing as typ
from pathlib import Path

import pytest
import vcr
from git import Repo
from pytest_bdd import parsers, then, when

from git_donkey import templates
from tests.git_repo_helpers import configure_repo
from tests.integration.plonk_helpers import removal_entry, unverified_entry
from tests.output_assertions import (
    GIT_ERROR_PLACEHOLDER,
    require_line,
    require_stream,
    require_terminal,
    stream,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from vcr.cassette import Cassette

    from tests.integration.donkey_helpers import DonkeyScenario
    from tests.integration.plonk_helpers import PlonkScenario

# Recorded GitHub API exchanges replayed by ``github_api_cassette``. The
# worktree commands under test talk to their remote over the Git protocol, which
# the temporary bare repositories in this package stand in for; the cassette
# covers the GitHub REST API, which they must never consult.
_CASSETTE_DIR = Path(__file__).parent / "cassettes"
_NO_GITHUB_API_CASSETTE = "github_api_no_interactions.yaml"


@dataclasses.dataclass(frozen=True, slots=True)
class StubCommands:
    """Filesystem locations for generated command stubs."""

    bin_dir: Path
    log_path: Path


@dataclasses.dataclass(frozen=True, slots=True)
class GitHubCliStub:
    """A recording, failing ``gh`` executable placed first on ``PATH``.

    The stub records every invocation and exits non-zero unless a payload file
    has been written for the scenario. That default makes an accidental GitHub
    lookup in a local-remote scenario fail closed; a scenario that expects the
    fallback writes a payload describing the merged pull request it wants.
    """

    bin_dir: Path
    log_path: Path
    payload_path: Path

    def calls(self) -> list[str]:
        """Return the argument lines recorded so far."""
        if not self.log_path.is_file():
            return []
        return self.log_path.read_text(encoding="utf-8").splitlines()

    def clear(self) -> None:
        """Remove the invocation log and any payload."""
        self.log_path.unlink(missing_ok=True)
        self.payload_path.unlink(missing_ok=True)

    def stub_merged_pull_request(
        self,
        *,
        number: int,
        head_branch: str,
        base_branch: str,
        owner: str,
    ) -> None:
        """Make the stub answer with one matching merged pull request."""
        payload = [
            {
                "number": number,
                "headRefName": head_branch,
                "baseRefName": base_branch,
                "headRepositoryOwner": {"login": owner},
            }
        ]
        self.payload_path.write_text(json.dumps(payload), encoding="utf-8")

    def stub_failure(self) -> None:
        """Make the stub exit non-zero without output."""
        self.payload_path.unlink(missing_ok=True)


_GH_STUB_TEMPLATE = """#!/usr/bin/env python3
from __future__ import annotations
import os
import sys
from pathlib import Path

log_path = Path(os.environ["GH_STUB_LOG"])
with log_path.open("a", encoding="utf-8") as handle:
    handle.write(" ".join(sys.argv[1:]) + "\\n")
payload_path = Path(os.environ["GH_STUB_PAYLOAD"])
if not payload_path.is_file():
    sys.exit(1)
sys.stdout.write(payload_path.read_text(encoding="utf-8"))
"""


@pytest.fixture(autouse=True)
def github_cli_stub(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> GitHubCliStub | None:
    """Install a recording, failing ``gh`` stub for every plonk BDD module.

    Local-remote scenarios must never reach the fallback, so their helper
    asserts the log stays empty. The GitHub feature opts in explicitly and
    writes the payload its scenario expects.

    Returns
    -------
    GitHubCliStub | None
        The installed stub, or ``None`` for modules that do not run git-plonk.

    """
    module_name = request.module.__name__.rsplit(".", 1)[-1]
    if not module_name.startswith("test_git_plonk"):
        return None
    bin_dir = tmp_path / "github-cli-bin"
    bin_dir.mkdir()
    stub_path = bin_dir / "gh"
    stub_path.write_text(_GH_STUB_TEMPLATE)
    stub_path.chmod(0o755)
    stub = GitHubCliStub(
        bin_dir=bin_dir,
        log_path=tmp_path / "gh-calls.log",
        payload_path=tmp_path / "gh-payload.json",
    )
    monkeypatch.setenv(
        "PATH",
        f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
    )
    monkeypatch.setenv("GH_STUB_LOG", str(stub.log_path))
    monkeypatch.setenv("GH_STUB_PAYLOAD", str(stub.payload_path))
    return stub


@pytest.fixture
def stub_commands(tmp_path: Path) -> StubCommands:
    """Create ``git`` and ``copier`` stubs that log invocations."""
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

    return StubCommands(bin_dir=bin_dir, log_path=log_path)


@pytest.fixture
def github_api_cassette() -> cabc.Iterator[Cassette]:
    """Replay the recorded GitHub API traffic, refusing any request outside it.

    The cassette is replayed in VCR's ``none`` record mode, so an HTTP request
    the recording does not contain raises inside the code under test instead
    of reaching the network. The recording holds no interactions: ``git
    donkey`` and ``git plonk`` judge completion and choose bases from Git
    history alone, and a test that provokes a GitHub API call fails here.

    Yields
    ------
    Cassette
        The replayed cassette, whose ``play_count`` and ``requests`` a test can
        assert on after the workflow ran.

    """
    recorder = vcr.VCR(
        record_mode="none",
        cassette_library_dir=_CASSETTE_DIR.as_posix(),
    )
    with recorder.use_cassette(_NO_GITHUB_API_CASSETTE) as cassette:
        yield cassette


def _setup_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Create a local repo with a bare remote and a seeded main branch."""
    # Imported locally: donkey_helpers imports ``_setup_repo`` from this
    # module at load time, so a module-level import here would be circular.
    from tests.integration.donkey_helpers import seed_repo

    remote_path = tmp_path / "remote.git"
    local_path = tmp_path / "local"

    remote_repo = Repo.init(remote_path, bare=True)
    local_repo = Repo.init(local_path)
    configure_repo(local_repo)
    local_repo.create_remote("origin", remote_path.as_posix())

    seed_repo(local_repo, "README.md", "seed")
    local_repo.git.branch("-M", "main")
    local_repo.remote("origin").push("main")
    remote_repo.git.symbolic_ref("HEAD", "refs/heads/main")

    return local_path, remote_path


@when("I run git donkey with the remote default base")
def run_donkey_with_remote_default_base(
    scenario: DonkeyScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run the workflow with an implicit base."""
    # Imported locally to avoid a circular import: donkey_helpers imports
    # ``_setup_repo`` from this module at load time.
    from tests.integration.donkey_helpers import run_donkey

    run_donkey(scenario, capsys)


@then("git donkey succeeds")
def git_donkey_succeeds(scenario: DonkeyScenario) -> None:
    """Check that the workflow reported success."""
    if scenario.exit_code != 0:
        pytest.fail(f"expected git donkey to succeed, got exit {scenario.exit_code}")


@then(parsers.parse('git donkey fails with code {code:d} and reports "{message}"'))
def git_donkey_fails_reporting(
    scenario: DonkeyScenario,
    code: int,
    message: str,
) -> None:
    """Check that the workflow exited with ``code`` and explained itself."""
    if scenario.exit_code != code:
        pytest.fail(f"expected git donkey to exit with {code}: {scenario.exit_code}")
    _require_stderr_message(scenario, message)


@then(parsers.parse('git donkey reports "{message}"'))
def git_donkey_reports(scenario: DonkeyScenario, message: str) -> None:
    """Check that the workflow wrote ``message`` to stderr."""
    _require_stderr_message(scenario, message)


def _worktree_holding_branch(scenario: DonkeyScenario) -> Path:
    """Return the registered worktree currently holding the scenario's branch."""
    porcelain = scenario.repo.git.worktree("list", "--porcelain")
    wanted_branch = f"branch refs/heads/{scenario.branch}"
    for stanza in porcelain.split("\n\n"):
        lines = stanza.splitlines()
        if lines and wanted_branch in lines[1:]:
            return Path(lines[0].removeprefix("worktree "))
    return scenario.worktree_path()


def _expand_stderr_template(scenario: DonkeyScenario, message: str) -> str:
    """Substitute the path placeholders a feature message may carry.

    ``{worktree_path}`` names the worktree that already holds the branch, which
    is what a refusal to reuse that branch reports. ``{target_path}`` names the
    worktree path ``git donkey`` would have created. ``{template_dir}`` names
    the overlay directory for the scenario's repository, and expands to an
    empty string when the repository has no remote.

    Returns
    -------
    str
        The message with every recognised placeholder replaced.

    """
    overlay_dir = templates.get_template_dir_path(scenario.repo)
    replacements = {
        "{worktree_path}": str(_worktree_holding_branch(scenario)),
        "{target_path}": str(scenario.worktree_path()),
        "{template_dir}": str(overlay_dir or ""),
    }
    for placeholder, value in replacements.items():
        message = message.replace(placeholder, value)
    return message


def _require_stderr_message(scenario: DonkeyScenario, message: str) -> None:
    """Fail unless the recorded stderr carries ``message`` as a whole line.

    A message ending in the Git-error placeholder is the tail of a failing
    run's stream, so its literal part is matched from a line boundary and the
    remainder is left to Git; everything else is matched as one complete line
    of a longer report.
    """
    expected = _expand_stderr_template(scenario, message)
    if GIT_ERROR_PLACEHOLDER in expected:
        require_terminal(scenario.stderr, expected, context="git donkey stderr")
        return
    require_line(scenario.stderr, expected, context="git donkey stderr")


def _require_plonk_entry(
    scenario: PlonkScenario,
    heading: str,
    entry: str,
) -> None:
    """Assert the report is exactly the header, heading, and one entry."""
    require_stream(
        scenario.stdout,
        stream(f"git-plonk: mode={scenario.mode}", heading, entry),
        context="git plonk stdout",
    )


_PLONK_ENTRY_STEP = (
    r"^git plonk reports the (?:"
    r'removal as "(?P<removal>[^"]+)"'
    r"|completed worktree as unverified with "
    r'"(?P<unverified>[^"]+)"'
    r")$"
)


@then(parsers.re(_PLONK_ENTRY_STEP))
def git_plonk_reports_entry(
    scenario: PlonkScenario,
    removal: str | None = None,
    unverified: str | None = None,
) -> None:
    """Assert the one removal or unverified entry the report names."""
    if removal is not None:
        _require_plonk_entry(
            scenario,
            "Removed worktrees:",
            removal_entry(
                scenario.worktree_path(scenario.completed_branch),
                removal,
            ),
        )
        return
    if unverified is None:
        msg = "expected a removal or unverified label"
        raise AssertionError(msg)
    _require_plonk_entry(
        scenario,
        "Unverified worktrees:",
        unverified_entry(
            scenario.worktree_path(scenario.completed_branch),
            unverified,
        ),
    )
