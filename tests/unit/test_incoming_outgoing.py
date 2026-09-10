"""Unit tests for incoming and outgoing comparison helpers."""

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

from git_donkey import incoming_outgoing, incoming_outgoing_policy

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


@dataclasses.dataclass(frozen=True, slots=True)
class _ComparisonHarness:
    """Bound comparison runner plus the remotes it has fetched."""

    run: typ.Callable[..., tuple[int, str, str]]
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
        runner: typ.Callable[..., int],
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


def _comparison_records(
    caplog: pytest.LogCaptureFixture,
) -> tuple[logging.LogRecord, logging.LogRecord]:
    """Return the captured comparison start and completion records."""
    started, completed = [
        record
        for record in caplog.records
        if getattr(record, "operation", None) == "compare"
    ]
    return started, completed


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


@pytest.mark.parametrize(
    "remote_names",
    [
        ["team", "team/core"],
        ["team/core", "team"],
    ],
)
def test_remote_name_for_ref_prefers_longest_match(remote_names: list[str]) -> None:
    """A nested remote must own the ref whatever the configured order."""
    owner = incoming_outgoing_policy.remote_name_for_ref(
        remote_names,
        "team/core/main",
    )

    assert owner == "team/core", (
        "the longest matching remote must own a nested remote ref"
    )


@pytest.mark.parametrize(
    ("remote_names", "ref", "expected"),
    [
        (["origin"], "origin", "origin"),
        (["origin"], "refs/remotes/origin/main", "origin"),
        (["team", "team/core"], "team/core", "team/core"),
        (["origin"], "main", None),
    ],
)
def test_remote_name_for_ref_matches_exact_and_prefixed_names(
    remote_names: list[str],
    ref: str,
    expected: str | None,
) -> None:
    """Exact names and slash-delimited prefixes keep selecting their remote."""
    owner = incoming_outgoing_policy.remote_name_for_ref(remote_names, ref)

    assert owner == expected, "the configured remote owning the ref must be returned"


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


def test_run_git_incoming_reports_missing_upstream(
    comparison: _ComparisonHarness,
) -> None:
    """A missing upstream should exit 2 and explain how to configure one."""
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
