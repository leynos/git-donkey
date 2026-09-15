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
import typing as typ
from pathlib import Path

import pytest
import vcr
from git import Repo
from pytest_bdd import parsers, then, when

from tests.git_repo_helpers import configure_repo

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from vcr.cassette import Cassette

    from tests.integration.donkey_helpers import DonkeyScenario

# Recorded GitHub API exchanges replayed by ``github_api_cassette``. The
# worktree commands under test talk to their remote over the Git protocol, which
# the temporary bare repositories in this package stand in for; the cassette
# covers the GitHub REST API, which they must never consult.
_CASSETTE_DIR = Path(__file__).parent / "cassettes"
_NO_GITHUB_API_CASSETTE = "github_api_no_interactions.yaml"

# Recorded GitHub API traffic ``git wheresat`` replays as its parent evidence,
# and the record mode it is replayed in. ``none`` is the mode a suite runs in,
# because a request the recordings do not hold must fail inside the code under
# test rather than reach the network; ``--record-mode`` is for the deliberate
# recording pass ``docs/developers-guide.md`` describes, and for nothing else.
_PARENT_METADATA_CASSETTE = "wheresat_parent_metadata.yaml"
_RATE_LIMITED_CASSETTE = "wheresat_rate_limited.yaml"
_REPLAY_ONLY: typ.Final = "none"


@dataclasses.dataclass(frozen=True, slots=True)
class StubCommands:
    """Filesystem locations for generated command stubs."""

    bin_dir: Path
    log_path: Path


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


def _recorder(record_mode: str) -> vcr.VCR:
    """Return a VCR recorder for one cassette, with the credential filtered out.

    Every cassette goes through this, so no recording can carry the token the
    requests were made with: ``filter_headers`` drops the header before the
    request is written, which is what makes the recordings safe to commit.
    Replay does not miss it, because an interaction is matched on the request's
    method and URL rather than on what it carried.

    Parameters
    ----------
    record_mode : str
        What the recorder does with requests the cassette does not hold.

    Returns
    -------
    vcr.VCR
        The recorder, which each fixture plays its cassette through.

    """
    return vcr.VCR(
        record_mode=record_mode,
        cassette_library_dir=_CASSETTE_DIR.as_posix(),
        filter_headers=["authorization"],
    )


@pytest.fixture
def github_api_cassette() -> cabc.Iterator[Cassette]:
    """Replay the recorded GitHub API traffic, refusing any request outside it.

    The cassette is replayed in VCR's ``none`` record mode, so an HTTP request
    the recording does not contain raises inside the code under test instead
    of reaching the network. The recording holds no interactions: ``git
    donkey`` and ``git plonk`` judge completion and choose bases from Git
    history alone, and a test that provokes a GitHub API call fails here.

    The mode is fixed rather than taken from ``--record-mode`` for that reason:
    this cassette's subject is the *absence* of API traffic, and a recording
    pass that could write into it would let a command that started calling the
    API record the call instead of failing the test that forbids it.

    Yields
    ------
    Cassette
        The replayed cassette, whose ``play_count`` and ``requests`` a test can
        assert on after the workflow ran.

    """
    with _recorder(_REPLAY_ONLY).use_cassette(
        _NO_GITHUB_API_CASSETTE,
        allow_playback_repeats=True,
    ) as cassette:
        yield cassette


@pytest.fixture(scope="module")
def wheresat_parent_metadata_cassette(
    request: pytest.FixtureRequest,
) -> cabc.Iterator[Cassette]:
    """Replay the parent evidence ``git wheresat`` reads from GitHub.

    The recording holds the repository's own merged squash pull request, its
    own open pull request, a stacked pull request whose position in the stack
    the run reads, and the pull requests associated with one commit. It is
    replayed in ``none`` record mode unless ``--record-mode`` says otherwise,
    so a run that asked GitHub a question the recording does not hold fails
    rather than reaching the network.

    Yields
    ------
    Cassette
        The replayed cassette, whose ``play_count`` a test can assert on to
        show its answer came from the recording.

    """
    record_mode = request.config.getoption("record_mode")
    with _recorder(record_mode).use_cassette(
        _PARENT_METADATA_CASSETTE,
        allow_playback_repeats=True,
    ) as cassette:
        yield cassette


@pytest.fixture(scope="module")
def wheresat_rate_limited_cassette(
    request: pytest.FixtureRequest,
) -> cabc.Iterator[Cassette]:
    """Replay GitHub's refusal of a request the credential's allowance covered.

    The recording holds one answer and it is not an answer: a ``403`` whose
    ``X-RateLimit-Remaining`` is ``0``. It is kept apart from the metadata
    recording because it is made by exhausting a real allowance, so re-recording
    it is a deliberate act with a cost rather than a side effect of the pass
    that refreshes the others.

    Yields
    ------
    Cassette
        The replayed cassette, whose ``play_count`` a test can assert on.

    """
    record_mode = request.config.getoption("record_mode")
    with _recorder(record_mode).use_cassette(_RATE_LIMITED_CASSETTE) as cassette:
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
    # The remote-tracking default alias, spelled out rather than imported.
    # Cloning creates it; fetching does not, and the alias is what lets the
    # commands identify the trunk from local refs alone, so a fixture that
    # omits it is not shaped like a clone.
    local_repo.git.symbolic_ref("refs/remotes/origin/HEAD", "refs/remotes/origin/main")

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


def _require_stderr_message(scenario: DonkeyScenario, message: str) -> None:
    """Fail the test unless ``message`` appears in the recorded stderr."""
    if message not in scenario.stderr:
        pytest.fail(f"expected stderr to report {message!r}: {scenario.stderr!r}")
