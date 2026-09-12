"""Bounded observability for the git-donkey workflow.

Workflow steps report what happened as a small record: an operation name, an
outcome, and the labels that step reports, such as the pull mode, the base kind,
the error class, the cleanup mode, or the reason a worktree was skipped. Every
attribute is drawn from a fixed vocabulary, so records stay aggregatable. Branch
names, filesystem paths, remote URLs, Git output, exception text, and template
directory names are never recorded: those values either have unbounded
cardinality or disclose local information.

Records are delivered to the process-wide recorder, which is a :class:`Recorder`
implementation installed with :func:`set_recorder`. The default is
:class:`NullRecorder`, so the workflow emits nothing until a caller opts in, and
this module never exports data, starts a process, or opens a connection.
Install :class:`LoggingRecorder` to route records through the same structured
stdlib logging convention the rest of the package uses, where each attribute
becomes an ``extra`` field::

    import logging

    from git_donkey import observability

    observability.set_recorder(
        observability.LoggingRecorder(logging.getLogger("git_donkey.donkey"))
    )

The timed operations are remote default discovery, default-branch fetch, pull
execution, worktree creation, comparison fetch, and comparison. A span reports
the operation name and its duration only; outcomes are reported by
:meth:`Recorder.record`.
"""

from __future__ import annotations

import contextlib
import dataclasses
import time
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import logging

type Operation = typ.Literal[
    "pull_mode_selection",
    "remote_default_discovery",
    "default_branch_fetch",
    "base_update",
    "pull_execution",
    "worktree_creation",
    "template_overlay",
    "comparison_fetch",
    "comparison",
    "worktree_preflight",
    "worktree_removal",
    "branch_deletion",
]
"""Fixed operation names a workflow step can report."""

type Outcome = typ.Literal[
    "success",
    "failure",
    "started",
    "selected",
    "rejected",
    "declined",
    "not_requested",
    "not_behind",
    "unavailable",
    "found",
    "empty",
    "skipped",
]
"""Fixed outcomes an operation can report."""

type PullModeLabel = typ.Literal["none", "rebase", "ff_only"]
"""Fixed labels for the selected base-update strategy."""

type BaseKind = typ.Literal["implicit_remote_default", "explicit"]
"""Fixed labels for how the base branch was selected."""

type ErrorKind = typ.Literal[
    "git_command_error",
    "missing_advertised_default",
    "base_not_in_worktree",
    "os_error",
    "selection_error",
    "worktree_creation_error",
]
"""Fixed labels for the class of failure an operation reported."""

type CleanupModeLabel = typ.Literal["default", "soft", "hard"]
"""Fixed labels for the git-plonk mode a cleanup step ran in."""

type SkipReasonLabel = typ.Literal["dirty", "unavailable", "removal_failed"]
"""Fixed labels for why a cleanup step left a worktree in place."""


@dataclasses.dataclass(frozen=True, slots=True)
class Observation:
    """One bounded record of a workflow step."""

    operation: Operation
    outcome: Outcome
    pull_mode: PullModeLabel | None = None
    base_kind: BaseKind | None = None
    error_kind: ErrorKind | None = None
    mode: CleanupModeLabel | None = None
    skip_reason: SkipReasonLabel | None = None

    def attributes(self) -> dict[str, str]:
        """Return the record's bounded attributes, omitting unset labels.

        Returns
        -------
        dict[str, str]
            Fixed-vocabulary attribute values keyed by attribute name.

        """
        return {
            name: value
            for name, value in (
                ("operation", self.operation),
                ("outcome", self.outcome),
                ("pull_mode", self.pull_mode),
                ("base_kind", self.base_kind),
                ("error_kind", self.error_kind),
                ("mode", self.mode),
                ("skip_reason", self.skip_reason),
            )
            if value is not None
        }


class Recorder(typ.Protocol):
    """Sink for bounded workflow observations and timed operations."""

    def record(self, observation: Observation, /) -> None:
        """Record one completed ``observation``."""

    def span(self, operation: Operation, /) -> contextlib.AbstractContextManager[None]:
        """Return a context manager timing ``operation``."""


class NullRecorder(Recorder):
    """Default recorder: it discards every observation and times nothing."""

    @typ.override
    def record(self, observation: Observation, /) -> None:
        """Discard ``observation``."""

    @typ.override
    def span(self, operation: Operation, /) -> contextlib.AbstractContextManager[None]:
        """Return a context manager that records nothing for ``operation``."""
        return contextlib.nullcontext()


class LoggingRecorder:
    """Recorder that writes bounded attributes through structured logging.

    Parameters
    ----------
    logger : logging.Logger
        Logger that receives one record per observation and per timed span.

    """

    def __init__(self, logger: logging.Logger) -> None:
        """Keep the logger that receives the emitted records."""
        self._logger = logger

    def record(self, observation: Observation, /) -> None:
        """Log ``observation`` as its bounded attributes."""
        self._logger.info(
            "git-donkey workflow observation",
            extra=observation.attributes(),
        )

    @contextlib.contextmanager
    def span(self, operation: Operation, /) -> cabc.Iterator[None]:
        """Log ``operation`` and its duration once the timed block finishes."""
        started = time.perf_counter()
        try:
            yield
        finally:
            self._logger.info(
                "git-donkey workflow duration",
                extra={
                    "operation": operation,
                    "duration_seconds": time.perf_counter() - started,
                },
            )


@dataclasses.dataclass(slots=True)
class _RecorderState:
    """Mutable holder for the process-wide workflow recorder."""

    recorder: Recorder = dataclasses.field(default_factory=NullRecorder)


_STATE = _RecorderState()


def get_recorder() -> Recorder:
    """Return the recorder that receives workflow observations.

    Returns
    -------
    Recorder
        The installed recorder, which discards records until a caller installs
        another one.

    """
    return _STATE.recorder


def set_recorder(recorder: Recorder) -> Recorder:
    """Install ``recorder`` for subsequent workflow runs.

    Parameters
    ----------
    recorder : Recorder
        Recorder that receives observations from now on.

    Returns
    -------
    Recorder
        The recorder that was installed before this call, so a caller can
        restore it when its own recording is finished.

    """
    previous = _STATE.recorder
    _STATE.recorder = recorder
    return previous
