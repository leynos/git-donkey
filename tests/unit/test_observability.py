"""Unit contracts for the bounded observability adapter.

These tests pin the adapter's contract: a record carries only fixed-vocabulary
attributes, the process default emits nothing, and the opt-in logging recorder
reuses the package's structured logging convention.
"""

from __future__ import annotations

import logging
import typing as typ

from git_donkey import observability
from tests.observability_helpers import RecordingRecorder, declared_attribute_values

if typ.TYPE_CHECKING:
    import pytest

# Every attribute name a bounded record may carry.
_BOUNDED_ATTRIBUTE_NAMES = frozenset({
    "operation",
    "outcome",
    "pull_mode",
    "base_kind",
    "error_kind",
    "mode",
    "skip_reason",
})

# Attributes logging adds to every record, excluded when reading the payload.
# ``message`` is added by the formatter once a handler formats the record.
_STANDARD_LOGRECORD_ATTRIBUTES = frozenset(logging.makeLogRecord({}).__dict__) | {
    "message"
}

_LOGGER_NAME = "git_donkey.donkey"


def _logged_attributes(record: logging.LogRecord) -> dict[str, object]:
    """Return the payload a logging recorder attached to ``record``."""
    return {
        name: value
        for name, value in record.__dict__.items()
        if name not in _STANDARD_LOGRECORD_ATTRIBUTES
    }


def test_observation_attributes_omit_unset_labels() -> None:
    """A record carries only the labels it sets."""
    observation = observability.Observation(
        operation="worktree_creation", outcome="started"
    )

    assert observation.attributes() == {
        "operation": "worktree_creation",
        "outcome": "started",
    }, "unset labels are omitted rather than recorded as empty values"


def test_observation_attributes_use_only_declared_vocabulary() -> None:
    """A fully labelled record stays inside the declared vocabularies."""
    observation = observability.Observation(
        operation="base_update",
        outcome="failure",
        pull_mode="rebase",
        base_kind="implicit_remote_default",
        error_kind="git_command_error",
        mode="hard",
        skip_reason="dirty",
    )

    attributes = observation.attributes()

    assert set(attributes) == _BOUNDED_ATTRIBUTE_NAMES, (
        "a record exposes exactly the bounded attribute names"
    )
    assert set(attributes.values()) <= declared_attribute_values(), (
        "every attribute value is drawn from a fixed vocabulary"
    )


def test_declared_attribute_values_are_populated() -> None:
    """The derived vocabulary is not vacuously empty."""
    assert {
        "success",
        "failure",
        "git_command_error",
        "implicit_remote_default",
        "hard",
        "dirty",
    } <= declared_attribute_values(), "the vocabularies declare their values"


def test_process_default_recorder_discards_records() -> None:
    """A process that installs no recorder emits nothing."""
    assert isinstance(observability.get_recorder(), observability.NullRecorder), (
        "the default recorder is the no-op implementation"
    )


def test_null_recorder_runs_the_timed_block() -> None:
    """The no-op recorder still runs the block it times."""
    recorder = observability.NullRecorder()
    recorder.record(observability.Observation("worktree_creation", "started"))

    entered: list[bool] = []
    with recorder.span("worktree_creation"):
        entered.append(True)

    assert entered == [True], "a no-op span runs the block without timing it"


def test_set_recorder_returns_the_previous_recorder(
    recording_recorder: RecordingRecorder,
) -> None:
    """Installing a recorder hands back the one it replaced."""
    replacement = observability.NullRecorder()

    assert observability.set_recorder(replacement) is recording_recorder, (
        "the installed recorder is returned to the caller"
    )
    assert observability.get_recorder() is replacement, (
        "the replacement becomes the active recorder"
    )
    assert observability.set_recorder(recording_recorder) is replacement, (
        "restoring hands back the replacement"
    )
    assert observability.get_recorder() is recording_recorder, (
        "the recording recorder is active again"
    )


def test_logging_recorder_logs_only_bounded_attributes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The logging recorder publishes exactly the bounded payload."""
    recorder = observability.LoggingRecorder(logging.getLogger(_LOGGER_NAME))
    observation = observability.Observation(
        operation="base_update",
        outcome="failure",
        pull_mode="ff_only",
        base_kind="explicit",
        error_kind="base_not_in_worktree",
    )

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        recorder.record(observation)

    assert len(caplog.records) == 1, "one observation logs one record"
    record = caplog.records[0]
    assert record.getMessage() == "git-donkey workflow observation", (
        "the message is a fixed template"
    )
    assert _logged_attributes(record) == observation.attributes(), (
        "the payload carries only the bounded attributes"
    )


def test_logging_recorder_span_logs_operation_and_duration(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A timed operation logs its name and duration and nothing else."""
    recorder = observability.LoggingRecorder(logging.getLogger(_LOGGER_NAME))

    with (
        caplog.at_level(logging.INFO, logger=_LOGGER_NAME),
        recorder.span("pull_execution"),
    ):
        pass

    assert len(caplog.records) == 1, "one span logs one record"
    record = caplog.records[0]
    assert record.getMessage() == "git-donkey workflow duration", (
        "the message is a fixed template"
    )
    attributes = _logged_attributes(record)
    assert set(attributes) == {"operation", "duration_seconds"}, (
        "a span reports the operation and its duration only"
    )
    assert attributes["operation"] == "pull_execution", "the span names the operation"
    assert isinstance(attributes["duration_seconds"], float), (
        "the span reports a measured duration"
    )
