"""Unit tests for incoming and outgoing comparison observability."""

from __future__ import annotations

import dataclasses
import logging
import types
import typing as typ

import pytest
from git import GitCommandError

from git_donkey import incoming_outgoing

if typ.TYPE_CHECKING:
    from tests.observability_helpers import RecordingRecorder

_LOG_COMMAND = "log"
_UPSTREAM_REF = "origin/main"

# git-donkey's own exit code for a command that could not run, as opposed to
# Mercurial's ``0`` (commits found) and ``1`` (comparison found nothing).
_COULD_NOT_RUN_EXIT_CODE = 2


def _noop_fetch(repo: object, remote: str, prefix: str) -> None:
    """Accept a comparison fetch without contacting a remote."""


class _FakeGit:
    """Minimal ``repo.git`` double for the comparison command surface."""

    def __init__(self, log_output: str, *, fail_log: bool = False) -> None:
        self.log_output = log_output
        self.fail_log = fail_log

    def log(self, *args: str) -> str:
        """Return the configured output, or fail the way GitPython does."""
        if self.fail_log:
            raise GitCommandError(_LOG_COMMAND, 128)
        return self.log_output

    @staticmethod
    def rev_parse(*args: str) -> str:
        """Return the configured upstream ref."""
        return _UPSTREAM_REF


class _FakeRepo:
    """Minimal repository double for the comparison command surface."""

    def __init__(self, log_output: str, *, fail_log: bool = False) -> None:
        self.git = _FakeGit(log_output, fail_log=fail_log)
        self.remotes = [types.SimpleNamespace(name="origin")]


class _FakeAdapter:
    """Comparison adapter double that records fetches and can fail one."""

    def __init__(self, *, fail_fetch: bool = False) -> None:
        self.fetched: list[str] = []
        self.fail_fetch = fail_fetch

    @staticmethod
    def log(*args: str) -> str:
        """Return no comparison output."""
        return ""

    @staticmethod
    def upstream_ref() -> str | None:
        """Report no configured upstream."""
        return None

    @staticmethod
    def remote_names() -> typ.Iterable[str]:
        """Report the configured remote names."""
        return ["origin"]

    def fetch_remote(self, remote: str) -> None:
        """Record the fetch, or raise the shared helper's failure exit."""
        if self.fail_fetch:
            raise SystemExit(1)
        self.fetched.append(remote)


class _ComparisonRunner(typ.Protocol):
    """Callable surface shared by the incoming and outgoing runners."""

    def __call__(self, ref: str | None = None, *, fetch: bool = True) -> int:
        """Run one comparison and return its process exit code."""


class _HarnessRunner(typ.Protocol):
    """Callable surface of the fake-repository comparison helper."""

    def __call__(
        self,
        repo: _FakeRepo,
        runner: _ComparisonRunner,
        *,
        ref: str | None = _UPSTREAM_REF,
        fetch: bool = True,
    ) -> tuple[int, str, str]:
        """Run ``runner`` against ``repo`` and return its exit code and output."""


@dataclasses.dataclass(frozen=True, slots=True)
class _ComparisonHarness:
    """Bound comparison runner for the comparison command surface."""

    run: _HarnessRunner


@pytest.fixture
def comparison(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> _ComparisonHarness:
    """Return a fake-repository comparison runner that captures its output."""

    def _run(
        repo: _FakeRepo,
        runner: _ComparisonRunner,
        *,
        ref: str | None = _UPSTREAM_REF,
        fetch: bool = True,
    ) -> tuple[int, str, str]:
        """Run one comparison against ``repo`` and capture its output."""
        monkeypatch.setattr(
            incoming_outgoing.helpers,
            "_find_repo",
            lambda _prefix: repo,
        )
        monkeypatch.setattr(incoming_outgoing.helpers, "_fetch_remote", _noop_fetch)
        exit_code = runner(ref, fetch=fetch)
        captured = capsys.readouterr()
        return exit_code, captured.out, captured.err

    return _ComparisonHarness(run=_run)


def _comparison_records(
    caplog: pytest.LogCaptureFixture,
) -> list[logging.LogRecord]:
    """Return the captured comparison records in emission order."""
    return [
        record
        for record in caplog.records
        if getattr(record, "operation", None) == "compare"
    ]


def _comparison_request(
    *,
    fetch: bool = True,
) -> incoming_outgoing._ComparisonRequest:
    """Return an incoming comparison request with the given fetch setting."""
    return incoming_outgoing._ComparisonRequest(
        prefix="git-incoming",
        direction="incoming",
        ref="origin/main",
        fetch=fetch,
    )


def test_fetch_comparison_remote_skips_when_disabled() -> None:
    """A request that disables fetching must not contact the remote."""
    adapter = _FakeAdapter()

    fetched = incoming_outgoing._fetch_comparison_remote(
        _comparison_request(fetch=False),
        adapter,
        "origin",
    )

    assert fetched, "a skipped fetch must report success"
    assert not adapter.fetched, "fetch=False must not contact the remote"


def test_fetch_comparison_remote_skips_unnamed_remote() -> None:
    """A comparison ref owned by no configured remote must not be fetched."""
    adapter = _FakeAdapter()

    fetched = incoming_outgoing._fetch_comparison_remote(
        _comparison_request(),
        adapter,
        None,
    )

    assert fetched, "a fetch with no remote to fetch must report success"
    assert not adapter.fetched, "an unnamed remote must not be fetched"


def test_fetch_comparison_remote_fetches_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A remote-backed request must fetch the remote and log both records."""
    adapter = _FakeAdapter()

    with caplog.at_level(logging.INFO, logger=incoming_outgoing.__name__):
        fetched = incoming_outgoing._fetch_comparison_remote(
            _comparison_request(),
            adapter,
            "origin",
        )

    assert fetched, "a successful fetch must report success"
    assert adapter.fetched == ["origin"], "the named remote must be fetched"
    selection, completion = caplog.records
    assert selection.levelno == logging.INFO, (
        "the fetch selection must be logged at INFO"
    )
    assert selection.getMessage() == "Fetching comparison remote", (
        "the fetch must log the operation it performs"
    )
    fields = vars(selection)
    assert fields["operation"] == "fetch", "the fetch log must carry operation=fetch"
    assert fields["direction"] == "incoming", (
        "the fetch log must carry the comparison direction"
    )
    assert fields["remote"] == "origin", "the fetch log must carry the remote name"
    assert completion.levelno == logging.INFO, (
        "the fetch completion must be logged at INFO"
    )
    assert completion.getMessage() == "Completed comparison fetch", (
        "a successful fetch must log its completion"
    )
    fields = vars(completion)
    assert fields["operation"] == "fetch", (
        "the completion log must carry operation=fetch"
    )
    assert fields["direction"] == "incoming", (
        "the completion log must carry the comparison direction"
    )
    assert fields["remote"] == "origin", "the completion log must carry the remote name"
    assert fields["result"] == "success", "the completion log must carry result=success"


def test_fetch_comparison_remote_reports_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A fetch that exits the process must report failure, not propagate."""
    adapter = _FakeAdapter(fail_fetch=True)

    with caplog.at_level(logging.INFO, logger=incoming_outgoing.__name__):
        fetched = incoming_outgoing._fetch_comparison_remote(
            _comparison_request(),
            adapter,
            "origin",
        )

    assert not fetched, "a failed fetch must report failure"
    assert not adapter.fetched, "a failed fetch must record no completed fetch"
    attempt, failure = caplog.records
    assert attempt.levelno == logging.INFO, "the attempt must still be logged at INFO"
    assert failure.levelno == logging.WARNING, "the failure must be logged at WARNING"
    assert failure.getMessage() == "Comparison fetch failed", (
        "the failure must log a diagnostic message"
    )
    fields = vars(failure)
    assert fields["operation"] == "fetch", "the failure log must carry operation=fetch"
    assert fields["direction"] == "incoming", (
        "the failure log must carry the comparison direction"
    )
    assert fields["remote"] == "origin", "the failure log must carry the remote name"
    assert fields["result"] == "failure", "the failure log must carry result=failure"


def test_run_git_incoming_logs_comparison_records(
    comparison: _ComparisonHarness,
    caplog: pytest.LogCaptureFixture,
    recording_recorder: RecordingRecorder,
) -> None:
    """An incoming comparison must log and record its start and completion."""
    caplog.set_level(logging.INFO, logger=incoming_outgoing.__name__)
    repo = _FakeRepo("abc1234 Remote commit")

    exit_code, _, _ = comparison.run(repo, incoming_outgoing.run_git_incoming)

    assert exit_code == 0, "matching commits must exit 0"
    started, completed = _comparison_records(caplog)
    assert started.levelno == logging.INFO, "the comparison start must be INFO"
    assert started.getMessage() == "Starting incoming comparison", (
        "the start record must name the comparison direction"
    )
    fields = vars(started)
    assert fields["operation"] == "compare", "the start record must carry operation"
    assert fields["direction"] == "incoming", (
        "the start record must carry the comparison direction"
    )
    assert fields["fetch_enabled"] is True, (
        "the start record must report that fetching is enabled"
    )
    assert fields["ref"] == "origin/main", (
        "the start record must carry the comparison ref"
    )
    assert completed.levelno == logging.INFO, "the completion record must be INFO"
    assert completed.getMessage() == "Completed incoming comparison", (
        "the completion record must name the comparison direction"
    )
    fields = vars(completed)
    assert fields["commit_count"] == 1, (
        "the completion record must count the commits it printed"
    )
    assert fields["result"] == "found", "a printed comparison must report found"
    assert [span.operation for span in recording_recorder.spans] == [
        "comparison_fetch",
        "comparison",
    ], "the fetch and the comparison must both be timed"
    assert recording_recorder.outcomes("comparison_fetch") == ["success"], (
        "a completed fetch must report success"
    )
    assert recording_recorder.outcomes("comparison") == ["found"], (
        "a non-empty comparison must report that it found commits"
    )


def test_run_git_outgoing_logs_empty_comparison_record(
    comparison: _ComparisonHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An outgoing comparison that finds nothing must log that outcome."""
    caplog.set_level(logging.INFO, logger=incoming_outgoing.__name__)
    repo = _FakeRepo("")

    exit_code, _, _ = comparison.run(
        repo,
        incoming_outgoing.run_git_outgoing,
        fetch=False,
    )

    assert exit_code == 1, "an empty comparison must exit 1"
    started, completed = _comparison_records(caplog)
    fields = vars(started)
    assert fields["direction"] == "outgoing", (
        "the start record must carry the outgoing direction"
    )
    assert fields["fetch_enabled"] is False, (
        "the start record must report that fetching is disabled"
    )
    fields = vars(completed)
    assert fields["direction"] == "outgoing", (
        "the completion record must carry the outgoing direction"
    )
    assert fields["commit_count"] == 0, "an empty comparison must count no commits"
    assert fields["result"] == "empty", "an empty comparison must report empty"


def test_run_git_incoming_reports_comparison_failure(
    comparison: _ComparisonHarness,
    caplog: pytest.LogCaptureFixture,
    recording_recorder: RecordingRecorder,
) -> None:
    """A failed ``git log`` should exit 2 with a diagnostic and a record."""
    caplog.set_level(logging.INFO, logger=incoming_outgoing.__name__)
    repo = _FakeRepo("", fail_log=True)

    exit_code, out, err = comparison.run(repo, incoming_outgoing.run_git_incoming)

    assert exit_code == _COULD_NOT_RUN_EXIT_CODE, "a failed comparison must exit 2"
    assert not out, "a failed comparison must print no commits"
    assert "comparison failed" in err, (
        "a failed comparison must report the Git failure on stderr"
    )
    failure = next(
        record
        for record in caplog.records
        if getattr(record, "result", None) == "failure"
    )
    assert failure.levelno == logging.ERROR, "a failed comparison must log an error"
    assert failure.getMessage() == "Comparison failed", (
        "a failed comparison must log a diagnostic message"
    )
    assert vars(failure)["operation"] == "compare", (
        "the failure record must carry operation=compare"
    )
    assert recording_recorder.outcomes("comparison") == ["failure"], (
        "a failed comparison must report failure"
    )
    assert recording_recorder.error_kinds("comparison") == ["git_command_error"], (
        "a failed comparison must report the class of failure"
    )


def test_run_git_incoming_reports_fetch_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    recording_recorder: RecordingRecorder,
) -> None:
    """A failed fetch should exit 2 instead of masquerading as no commits."""

    def _fail_fetch(repo: object, remote: str, prefix: str) -> None:
        """Simulate the shared fetch helper's failure exit."""
        raise SystemExit(1)

    repo = _FakeRepo("")
    monkeypatch.setattr(
        incoming_outgoing.helpers,
        "_find_repo",
        lambda _prefix: repo,
    )
    monkeypatch.setattr(incoming_outgoing.helpers, "_fetch_remote", _fail_fetch)

    exit_code = incoming_outgoing.run_git_incoming()

    assert exit_code == _COULD_NOT_RUN_EXIT_CODE, "a failed fetch must exit 2, not 1"
    assert not capsys.readouterr().out, "a failed fetch must print no commits"
    assert recording_recorder.outcomes("comparison_fetch") == ["failure"], (
        "a failed fetch must report failure"
    )
    assert recording_recorder.error_kinds("comparison_fetch") == [
        "git_command_error"
    ], "a failed fetch must report the class of failure"
    assert recording_recorder.outcomes("comparison") == [], (
        "a failed fetch must not report a completed comparison"
    )
