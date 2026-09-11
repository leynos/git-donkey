"""Unit tests for the incoming and outgoing comparison workflows."""

from __future__ import annotations

import contextlib
import dataclasses
import io
import logging
import typing as typ

import pytest
from git import GitCommandError
from hypothesis import given
from hypothesis import strategies as st

from git_donkey import incoming_outgoing

if typ.TYPE_CHECKING:
    from tests.observability_helpers import RecordingRecorder

_COMMIT_ID = st.from_regex(r"[0-9a-f]{7}", fullmatch=True)
_LOG_COMMAND = "log"
_REV_PARSE_COMMAND = "rev-parse"

# git-donkey's own exit code for a command that could not run, as opposed to
# Mercurial's ``0`` (commits found) and ``1`` (comparison found nothing).
_COULD_NOT_RUN_EXIT_CODE = 2


class _FakeGit:
    """Minimal ``repo.git`` double for comparison-range tests."""

    def __init__(
        self,
        log_output: str,
        *,
        upstream: str | None = "origin/main",
        fail_log: bool = False,
    ) -> None:
        self.log_output = log_output
        self.upstream = upstream
        self.fail_log = fail_log
        self.calls: list[tuple[str, ...]] = []

    def log(self, *args: str) -> str:
        """Record log arguments and return the configured output."""
        self.calls.append(args)
        if self.fail_log:
            raise GitCommandError(_LOG_COMMAND, 128)
        return self.log_output

    def rev_parse(self, *args: str) -> str:
        """Return the configured upstream, or report a missing upstream."""
        if self.upstream is None:
            raise GitCommandError(_REV_PARSE_COMMAND, 128)
        return self.upstream


@dataclasses.dataclass(frozen=True, slots=True)
class _FakeRemote:
    """Minimal ``repo.remotes`` entry exposing a remote name."""

    name: str


class _FakeReference:
    """Minimal ``repo.head.reference`` double."""

    def __init__(self, *, tracking: bool) -> None:
        self._tracking = tracking

    def tracking_branch(self) -> _FakeRemote | None:
        """Report a configured upstream as a non-``None`` tracking branch."""
        return _FakeRemote("origin") if self._tracking else None


class _FakeHead:
    """Minimal ``repo.head`` double reporting a checked-out branch."""

    def __init__(self, *, tracking: bool) -> None:
        self.is_detached = False
        self.reference = _FakeReference(tracking=tracking)


class _FakeRepo:
    """Minimal repository double with a ``git`` command surface."""

    def __init__(
        self,
        log_output: str,
        *,
        upstream: str | None = "origin/main",
        fail_log: bool = False,
        remotes: tuple[str, ...] = ("origin",),
    ) -> None:
        self.git = _FakeGit(log_output, upstream=upstream, fail_log=fail_log)
        self.remotes = [_FakeRemote(name) for name in remotes]
        self.head = _FakeHead(tracking=upstream is not None)


class _ModelGit:
    """``repo.git`` double that models ref reachability as commit sets."""

    def __init__(self, reachable: dict[str, set[str]]) -> None:
        self.reachable = reachable

    def log(self, *args: str) -> str:
        """Return commits in the include ref and not in the exclude ref."""
        _, _, include_ref, _, exclude_ref = args
        unique = sorted(self.reachable[include_ref] - self.reachable[exclude_ref])
        return "\n".join(unique)


class _ModelRepo:
    """Repository double whose ``git`` models a bounded commit graph."""

    def __init__(self, reachable: dict[str, set[str]]) -> None:
        self.git = _ModelGit(reachable)
        self.remotes = [_FakeRemote("origin")]


class _ComparisonRunner(typ.Protocol):
    """Callable surface shared by the incoming and outgoing runners."""

    def __call__(self, ref: str | None = None, *, fetch: bool = True) -> int:
        """Run one comparison and return its process exit code."""


class _HarnessRunner(typ.Protocol):
    """Callable surface of the harness's fake-repository comparison helper."""

    def __call__(
        self,
        repo: _FakeRepo,
        runner: _ComparisonRunner,
        *,
        ref: str | None = "origin/main",
        fetch: bool = True,
    ) -> tuple[int, str, str]:
        """Run ``runner`` against ``repo`` and return its exit code and output."""


@dataclasses.dataclass(frozen=True, slots=True)
class _ComparisonHarness:
    """Bound comparison runner plus the remotes it has fetched."""

    run: _HarnessRunner
    fetches: list[str]


@pytest.fixture
def comparison(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> _ComparisonHarness:
    """Return a fake-repository comparison runner and its fetch recorder."""
    fetches: list[str] = []

    def _fetch(repo: object, remote: str, prefix: str) -> None:
        """Record the remote that a comparison fetched."""
        fetches.append(remote)

    def _run(
        repo: _FakeRepo,
        runner: _ComparisonRunner,
        *,
        ref: str | None = "origin/main",
        fetch: bool = True,
    ) -> tuple[int, str, str]:
        """Run one comparison against ``repo`` and capture its output."""
        monkeypatch.setattr(
            incoming_outgoing.helpers,
            "_find_repo",
            lambda _prefix: repo,
        )
        monkeypatch.setattr(incoming_outgoing.helpers, "_fetch_remote", _fetch)
        fetches.clear()
        exit_code = runner(ref, fetch=fetch)
        captured = capsys.readouterr()
        return exit_code, captured.out, captured.err

    return _ComparisonHarness(run=_run, fetches=fetches)


def _format_commits(commits: set[str]) -> str:
    """Return the ``git log`` output expected for ``commits``."""
    if not commits:
        return ""
    return "\n".join(sorted(commits)) + "\n"


def _comparison_record(caplog: pytest.LogCaptureFixture) -> logging.LogRecord:
    """Return the single captured comparison record the test expects."""
    return next(
        record
        for record in caplog.records
        if getattr(record, "operation", None) == "compare"
    )


def test_commits_unique_to_ref_returns_log_lines() -> None:
    """Comparison queries should return concise ``git log`` lines."""
    repo = _FakeRepo("abc1234 Remote commit")

    output = incoming_outgoing._commits_unique_to(
        repo.git,
        include_ref="origin/main",
        exclude_ref="HEAD",
    )

    assert output == "abc1234 Remote commit", (
        "the query must return the log output for the comparison range"
    )
    assert repo.git.calls == [
        (
            "--oneline",
            "--decorate",
            "origin/main",
            "--not",
            "HEAD",
        )
    ], "incoming comparisons must include the ref and exclude HEAD"


def test_commits_unique_to_ref_handles_empty_log() -> None:
    """No comparison commits should return no output."""
    repo = _FakeRepo("")

    output = incoming_outgoing._commits_unique_to(
        repo.git,
        include_ref="HEAD",
        exclude_ref="origin/main",
    )

    assert not output, "an empty comparison range must return no commits"


def test_run_git_incoming_reports_missing_upstream(
    comparison: _ComparisonHarness,
    caplog: pytest.LogCaptureFixture,
    recording_recorder: RecordingRecorder,
) -> None:
    """A missing upstream should exit 2 and explain how to configure one."""
    caplog.set_level(logging.INFO, logger=incoming_outgoing.__name__)
    repo = _FakeRepo("", upstream=None)

    exit_code, out, err = comparison.run(
        repo,
        incoming_outgoing.run_git_incoming,
        ref=None,
    )

    assert exit_code == _COULD_NOT_RUN_EXIT_CODE, "a missing upstream must exit 2"
    assert not out, "a missing upstream must print no commits"
    assert "no upstream branch configured" in err, (
        "a missing upstream must explain that no upstream is configured"
    )
    assert "pass a ref" in err, (
        "a missing upstream must suggest passing an explicit ref"
    )
    record = _comparison_record(caplog)
    assert record.levelno == logging.INFO, "a missing upstream must log at INFO"
    assert record.getMessage() == "No upstream configured for the comparison", (
        "a missing upstream must log why the comparison did not run"
    )
    fields = vars(record)
    assert fields["direction"] == "incoming", (
        "the missing-upstream log must carry the direction"
    )
    assert fields["result"] == "unavailable", (
        "a missing upstream must log result=unavailable"
    )
    assert recording_recorder.outcomes("comparison") == ["unavailable"], (
        "a missing upstream must record an unavailable comparison"
    )
    assert recording_recorder.error_kinds("comparison") == [], (
        "an unset upstream is not an error and must record no error kind"
    )


def test_run_git_incoming_reports_upstream_lookup_failure(
    comparison: _ComparisonHarness,
    caplog: pytest.LogCaptureFixture,
    recording_recorder: RecordingRecorder,
) -> None:
    """A configured upstream that cannot resolve must not look like no upstream."""
    caplog.set_level(logging.INFO, logger=incoming_outgoing.__name__)
    # A branch that configures an upstream rev-parse cannot resolve models the
    # state the lookup must report, as distinct from an unset upstream.
    repo = _FakeRepo("", upstream=None)
    repo.head = _FakeHead(tracking=True)

    exit_code, out, err = comparison.run(
        repo,
        incoming_outgoing.run_git_incoming,
        ref=None,
    )

    assert exit_code == _COULD_NOT_RUN_EXIT_CODE, "a failed lookup must exit 2"
    assert not out, "a failed lookup must print no commits"
    assert "upstream lookup failed" in err, (
        "a failed lookup must report the lookup itself"
    )
    assert "no upstream branch configured" not in err, (
        "a failed lookup must not be reported as a missing upstream"
    )
    record = _comparison_record(caplog)
    assert record.levelno == logging.WARNING, "a failed lookup must log at WARNING"
    assert record.getMessage() == "Upstream lookup failed", (
        "a failed lookup must log a diagnostic message"
    )
    fields = vars(record)
    assert fields["direction"] == "incoming", (
        "the failure log must carry the comparison direction"
    )
    assert fields["result"] == "failure", "the failure log must carry result=failure"
    assert recording_recorder.outcomes("comparison") == ["failure"], (
        "a failed lookup must record a failed comparison"
    )
    assert recording_recorder.error_kinds("comparison") == ["git_command_error"], (
        "a failed lookup must record the resolved error kind"
    )


def test_run_git_incoming_fetches_remote_backed_ref(
    comparison: _ComparisonHarness,
) -> None:
    """Remote-backed comparison refs should fetch their remote by default."""
    repo = _FakeRepo("abc1234 Remote commit")

    exit_code, out, err = comparison.run(repo, incoming_outgoing.run_git_incoming)

    assert exit_code == 0, "matching commits must exit 0"
    assert out == "abc1234 Remote commit\n", "the fetched commit must be printed"
    assert not err, "a successful comparison must not write to stderr"
    assert comparison.fetches == ["origin"], "remote-backed refs must fetch origin"


def test_run_git_incoming_fetches_canonical_remote_ref(
    comparison: _ComparisonHarness,
) -> None:
    """Canonical remote-tracking refs should fetch their owning remote."""
    repo = _FakeRepo("abc1234 Remote commit")

    exit_code, _, _ = comparison.run(
        repo,
        incoming_outgoing.run_git_incoming,
        ref="refs/remotes/origin/main",
    )

    assert exit_code == 0, "canonical refs must compare successfully"
    assert comparison.fetches == ["origin"], (
        "canonical refs/remotes refs must fetch their owning remote"
    )


def test_run_git_incoming_skips_fetch_for_local_ref(
    comparison: _ComparisonHarness,
) -> None:
    """Local comparison refs should not trigger a fetch."""
    repo = _FakeRepo("")

    exit_code, _, _ = comparison.run(
        repo,
        incoming_outgoing.run_git_incoming,
        ref="main",
    )

    assert exit_code == 1, "an empty comparison must exit 1"
    assert comparison.fetches == [], "local refs must not fetch a remote"


def test_run_git_incoming_no_fetch_skips_remote(
    comparison: _ComparisonHarness,
    recording_recorder: RecordingRecorder,
) -> None:
    """--no-fetch should compare without contacting the remote."""
    repo = _FakeRepo("")

    exit_code, _, _ = comparison.run(
        repo,
        incoming_outgoing.run_git_incoming,
        fetch=False,
    )

    assert exit_code == 1, "an empty comparison must exit 1"
    assert comparison.fetches == [], "--no-fetch must not contact the remote"
    assert recording_recorder.outcomes("comparison_fetch") == ["not_requested"], (
        "--no-fetch must report that the fetch was not requested"
    )


def test_run_git_outgoing_compares_head_against_ref(
    comparison: _ComparisonHarness,
) -> None:
    """Outgoing comparisons should include HEAD and exclude the comparison ref."""
    repo = _FakeRepo("abc1234 Local commit")

    exit_code, out, err = comparison.run(repo, incoming_outgoing.run_git_outgoing)

    assert exit_code == 0, "local-only commits must exit 0"
    assert out == "abc1234 Local commit\n", "the local-only commit must be printed"
    assert not err, "a successful comparison must not write to stderr"
    assert repo.git.calls == [
        ("--oneline", "--decorate", "HEAD", "--not", "origin/main")
    ], "outgoing comparisons must include HEAD and exclude the ref"


@given(
    head_commits=st.sets(_COMMIT_ID, max_size=4),
    remote_commits=st.sets(_COMMIT_ID, max_size=4),
)
def test_incoming_and_outgoing_mirror_reachability_model(
    head_commits: set[str],
    remote_commits: set[str],
) -> None:
    """Both directions should print the model's directional set difference."""
    repo = _ModelRepo({"HEAD": head_commits, "origin/main": remote_commits})
    output = io.StringIO()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            incoming_outgoing.helpers,
            "_find_repo",
            lambda _prefix: repo,
        )
        with contextlib.redirect_stdout(output):
            incoming_code = incoming_outgoing.run_git_incoming(
                "origin/main",
                fetch=False,
            )
            incoming_out = output.getvalue()
            output.seek(0)
            output.truncate()
            outgoing_code = incoming_outgoing.run_git_outgoing(
                "origin/main",
                fetch=False,
            )
            outgoing_out = output.getvalue()

    expected_incoming = remote_commits - head_commits
    expected_outgoing = head_commits - remote_commits

    assert incoming_out == _format_commits(expected_incoming), (
        "incoming must print commits reachable from the ref and not HEAD"
    )
    assert incoming_code == (0 if expected_incoming else 1), (
        "incoming must exit 0 when commits are printed and 1 otherwise"
    )
    assert outgoing_out == _format_commits(expected_outgoing), (
        "outgoing must print commits reachable from HEAD and not the ref"
    )
    assert outgoing_code == (0 if expected_outgoing else 1), (
        "outgoing must exit 0 when commits are printed and 1 otherwise"
    )
