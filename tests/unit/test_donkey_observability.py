"""Unit contracts for workflow observability emission.

These tests drive the instrumented workflow functions directly, with the Git
facade stubbed so that the failure branches are exact. The integration suite
covers the same records against real repositories.
"""

from __future__ import annotations

import dataclasses
import typing as typ

import pytest
from git import Git, GitCommandError, Repo

from git_donkey import donkey

if typ.TYPE_CHECKING:
    from pathlib import Path

    from tests.observability_helpers import RecordingRecorder

# Exit status reserved for a command-line usage error.
_USAGE_ERROR_EXIT_CODE = 2

# A symbolic HEAD a remote advertises for its default branch.
_DEFAULT_ADVERTISEMENT = "ref: refs/heads/trunk\tHEAD\nabc\tHEAD"


def _raise_git_command_error(*_args: object, **_kwargs: object) -> typ.NoReturn:
    """Raise the failure a Git command reports when it cannot run."""
    msg = "fatal: simulated command failure"
    raise GitCommandError(("git", "fetch"), 128, msg)


def _accept_git_command(*_args: object, **_kwargs: object) -> None:
    """Accept a Git command without running it."""


def _stub_git_command(
    monkeypatch: pytest.MonkeyPatch, name: str, replacement: object
) -> None:
    """Replace a Git command GitPython resolves dynamically."""
    monkeypatch.setattr(Git, name, replacement, raising=False)


def _stub_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    advertisement: str | None,
    *,
    fetch_error: bool = False,
) -> donkey._DonkeyContext:
    """Return a context whose remote answers with ``advertisement``.

    ``None`` makes remote discovery fail the way an unreachable remote does.

    Returns
    -------
    donkey._DonkeyContext
        The context whose Git facade answers without running a command.

    """
    repo = Repo.init(tmp_path)
    _stub_git_command(
        monkeypatch,
        "ls_remote",
        _raise_git_command_error
        if advertisement is None
        else lambda *_args, **_kwargs: advertisement,
    )
    _stub_git_command(
        monkeypatch,
        "fetch",
        _raise_git_command_error if fetch_error else _accept_git_command,
    )
    return donkey._DonkeyContext(
        repo_home=repo,
        remote="origin",
        branch_to_worktree={},
        worktrees_root=tmp_path / "worktrees",
    )


@dataclasses.dataclass(frozen=True, slots=True)
class _PullSelection:
    """One pull-mode choice and the bounded record it must emit."""

    options: donkey._PullOptions
    mode: str | None
    outcome: str
    label: str


_PULL_SELECTIONS = (
    _PullSelection(donkey._PullOptions(), None, "not_requested", "none"),
    _PullSelection(
        donkey._PullOptions(pull_rebase=True), "--rebase", "selected", "rebase"
    ),
    _PullSelection(
        donkey._PullOptions(pull_ff=True), "--ff-only", "selected", "ff_only"
    ),
)


@pytest.mark.parametrize(
    "selection", _PULL_SELECTIONS, ids=("none", "rebase", "ff_only")
)
def test_pull_mode_selection_records_each_choice(
    recording_recorder: RecordingRecorder,
    selection: _PullSelection,
) -> None:
    """Each pull-mode selection records its bounded outcome and label."""
    assert donkey._pull_mode(selection.options, no_pull=False) == selection.mode, (
        "the selected mode is unchanged"
    )

    records = recording_recorder.observations

    assert len(records) == 1, "one selection records one observation"
    record = records[0]
    assert record.operation == "pull_mode_selection", "the operation names the step"
    assert record.outcome == selection.outcome, "the outcome names the selection"
    assert record.pull_mode == selection.label, "the label names the selected mode"


def test_conflicting_pull_options_record_rejection(
    recording_recorder: RecordingRecorder,
) -> None:
    """Conflicting flags record a rejection and stay a usage error."""
    options = donkey._PullOptions(pull_rebase=True, pull_ff=True)

    with pytest.raises(SystemExit) as excinfo:
        donkey._pull_mode(options, no_pull=False)

    assert excinfo.value.code == _USAGE_ERROR_EXIT_CODE, (
        "conflicting options are a usage error"
    )
    assert recording_recorder.outcomes("pull_mode_selection") == ["rejected"], (
        "the rejected selection is recorded"
    )


def test_base_update_not_requested_records_the_skip(
    tmp_path: Path,
    recording_recorder: RecordingRecorder,
) -> None:
    """Without a pull mode the skipped update is the only record."""
    context = donkey._DonkeyContext(
        repo_home=Repo.init(tmp_path),
        remote="origin",
        branch_to_worktree={},
        worktrees_root=tmp_path,
    )

    donkey._maybe_update_base_branch(
        context,
        base_branch="main",
        pull_mode=None,
        base_kind="implicit_remote_default",
    )

    assert recording_recorder.outcomes("base_update") == ["not_requested"], (
        "the skipped update is recorded"
    )
    assert recording_recorder.base_kinds("base_update") == [
        "implicit_remote_default"
    ], "the record names how the base was selected"
    assert recording_recorder.span_operations() == [], "no update work is timed"


def test_discovered_default_records_discovery_and_fetch_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_recorder: RecordingRecorder,
) -> None:
    """A discovered default branch records both successful steps."""
    context = _stub_context(tmp_path, monkeypatch, _DEFAULT_ADVERTISEMENT)

    remote_ref = donkey._fetch_remote_default_ref(context)

    assert remote_ref == "refs/remotes/origin/trunk", "the fetched ref is returned"
    assert recording_recorder.outcomes("remote_default_discovery") == ["success"], (
        "discovery reports success"
    )
    assert recording_recorder.outcomes("default_branch_fetch") == ["success"], (
        "the explicit fetch reports success"
    )
    assert recording_recorder.span_operations() == [
        "remote_default_discovery",
        "default_branch_fetch",
    ], "both steps are timed in workflow order"
    assert all(span.duration_seconds >= 0 for span in recording_recorder.spans), (
        "each span records a measured duration"
    )


def test_unreachable_remote_records_discovery_git_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_recorder: RecordingRecorder,
) -> None:
    """A failing ls-remote records a Git command failure."""
    context = _stub_context(tmp_path, monkeypatch, None)

    with pytest.raises(SystemExit) as excinfo:
        donkey._fetch_remote_default_ref(context)

    assert excinfo.value.code == 1, "an undiscoverable default is an error"
    assert recording_recorder.outcomes("remote_default_discovery") == ["failure"], (
        "discovery reports the failure"
    )
    assert recording_recorder.error_kinds("remote_default_discovery") == [
        "git_command_error"
    ], "the failure class is the Git command"
    assert recording_recorder.span_operations() == ["remote_default_discovery"], (
        "the failed discovery is timed and the fetch never starts"
    )
    assert recording_recorder.leaked_details(("simulated", "fatal")) == set(), (
        "the Git error text is not recorded"
    )


def test_missing_advertised_default_records_the_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_recorder: RecordingRecorder,
) -> None:
    """A remote that advertises no HEAD branch records that failure."""
    context = _stub_context(tmp_path, monkeypatch, "abc\tHEAD")

    with pytest.raises(SystemExit) as excinfo:
        donkey._fetch_remote_default_ref(context)

    assert excinfo.value.code == 1, "a missing advertised default is an error"
    assert recording_recorder.outcomes("remote_default_discovery") == ["failure"], (
        "discovery reports the failure"
    )
    assert recording_recorder.error_kinds("remote_default_discovery") == [
        "missing_advertised_default"
    ], "the failure class is the missing advertised default"
    assert recording_recorder.span_operations() == ["remote_default_discovery"], (
        "the failed discovery is timed and the fetch never starts"
    )


def test_failed_default_fetch_records_git_failure_after_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_recorder: RecordingRecorder,
) -> None:
    """A discovered default that cannot be fetched records both steps."""
    context = _stub_context(
        tmp_path, monkeypatch, _DEFAULT_ADVERTISEMENT, fetch_error=True
    )

    with pytest.raises(SystemExit) as excinfo:
        donkey._fetch_remote_default_ref(context)

    assert excinfo.value.code == 1, "an unfetchable default is an error"
    assert recording_recorder.outcomes("remote_default_discovery") == ["success"], (
        "discovery still reports success"
    )
    assert recording_recorder.outcomes("default_branch_fetch") == ["failure"], (
        "the explicit fetch reports the failure"
    )
    assert recording_recorder.error_kinds("default_branch_fetch") == [
        "git_command_error"
    ], "the failure class is the Git command"
    assert recording_recorder.leaked_details(("simulated", "trunk")) == set(), (
        "neither the Git error text nor the branch name is recorded"
    )
