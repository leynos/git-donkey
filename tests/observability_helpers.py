"""Recorder doubles that capture workflow observability for assertions.

The unit and integration suites both inspect the bounded records the workflow
emits. This module holds the recording recorder and the vocabulary check; the
root ``conftest`` installs it for a single test.
"""

from __future__ import annotations

import contextlib
import dataclasses
import time
import typing as typ

from git_donkey import observability

if typ.TYPE_CHECKING:
    import collections.abc as cabc


@dataclasses.dataclass(frozen=True, slots=True)
class RecordedSpan:
    """One timed operation a recording recorder observed.

    Attributes
    ----------
    operation : observability.Operation
        The operation named when the timed block was entered.
    duration_seconds : float
        Wall-clock duration of the timed block.

    """

    operation: observability.Operation
    duration_seconds: float


def declared_attribute_values() -> frozenset[str]:
    """Return every value the bounded observability vocabularies declare.

    Returns
    -------
    frozenset[str]
        Fixed-vocabulary values a workflow record may carry.

    """
    return frozenset(
        value
        for alias in (
            observability.Operation,
            observability.Outcome,
            observability.PullModeLabel,
            observability.BaseKind,
            observability.ErrorKind,
        )
        for value in typ.get_args(alias.__value__)
    )


class RecordingRecorder(observability.Recorder):
    """Recorder that retains every observation and timed span it receives."""

    def __init__(self) -> None:
        self.observations: list[observability.Observation] = []
        self.spans: list[RecordedSpan] = []

    @typ.override
    def record(self, observation: observability.Observation, /) -> None:
        """Retain one bounded ``observation``."""
        self.observations.append(observation)

    @contextlib.contextmanager
    def span(self, operation: observability.Operation, /) -> cabc.Iterator[None]:
        """Retain ``operation`` and the duration of the timed block."""
        started = time.perf_counter()
        try:
            yield
        finally:
            self.spans.append(RecordedSpan(operation, time.perf_counter() - started))

    def outcomes(
        self, operation: observability.Operation
    ) -> list[observability.Outcome]:
        """Return the outcomes recorded for ``operation``, in order."""
        return [
            observation.outcome
            for observation in self.observations
            if observation.operation == operation
        ]

    def error_kinds(
        self, operation: observability.Operation
    ) -> list[observability.ErrorKind]:
        """Return the error kinds recorded for ``operation``, in order."""
        return [
            observation.error_kind
            for observation in self.observations
            if observation.operation == operation and observation.error_kind is not None
        ]

    def base_kinds(
        self, operation: observability.Operation
    ) -> list[observability.BaseKind]:
        """Return the base kinds recorded for ``operation``, in order."""
        return [
            observation.base_kind
            for observation in self.observations
            if observation.operation == operation and observation.base_kind is not None
        ]

    def pull_modes(
        self, operation: observability.Operation
    ) -> list[observability.PullModeLabel]:
        """Return the pull-mode labels recorded for ``operation``, in order."""
        return [
            observation.pull_mode
            for observation in self.observations
            if observation.operation == operation and observation.pull_mode is not None
        ]

    def span_operations(self) -> list[observability.Operation]:
        """Return the timed operations, in the order they finished."""
        return [span.operation for span in self.spans]

    def attribute_values(self) -> set[str]:
        """Return every attribute value this recorder has seen."""
        return {
            value
            for observation in self.observations
            for value in observation.attributes().values()
        }

    def unbounded_values(self) -> set[str]:
        """Return recorded values outside the declared bounded vocabularies."""
        return self.attribute_values() - declared_attribute_values()

    def leaked_details(self, details: cabc.Iterable[str]) -> set[str]:
        """Return recorded values that contain any of ``details``."""
        return {
            value
            for value in self.attribute_values()
            if any(detail in value for detail in details)
        }
