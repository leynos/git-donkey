"""The failures a ``git wheresat`` run can report, and how Git words one.

The graph failures are the ones a read of the repository produces: both halves
of the read-only Git port raise them, and the refusal a shallow clone produces
has to be the same class whichever half asked the question, so the vocabulary
lives below both rather than in the reader that history questions happen to be
asked through. The usage failure is the one the command itself produces, and it
lives here because two parts of the command raise it — the resolution that
turns the command line into object IDs, and the writes a run was asked to
make — and the workflow that reports it must not have to name either.

Nothing here reads anything. :func:`_reported` turns one failed command into
the single line a message can carry: a read that fails is reported as
indeterminate rather than read as a negative answer (INV-5), so what Git said
is the whole of what an operator has to act on.

"""

from __future__ import annotations


class WheresatGraphError(RuntimeError):
    """A question the repository could not answer.

    Raised for every way a read can fail — a missing object, an unreadable
    ref, a Git command that exited with a status it does not use for answers.
    The caller reports the run as indeterminate rather than reading the
    failure as a negative answer (INV-5).

    """


class ShallowHistoryError(WheresatGraphError):
    """A history question a graft makes unanswerable.

    Separate from its parent so a caller can label the refusal for the
    operator: "this clone needs deepening" is actionable in a way that "a Git
    command failed" is not, and the report says which of the two it read. A
    shallow refusal carries the same weight as any other fault — the run is
    indeterminate — so this class changes what a record names, never what a
    verdict may conclude.

    """


class WheresatUsageError(RuntimeError):
    """The run could not start, or could not carry out what it was asked to write.

    Distinct from a graph failure in what the run reports: a question the
    repository could not answer withholds the answer as indeterminate, while an
    unusable command line, a missing remote, and a record that cannot be written
    where the user said it must are all the same thing to the operator — the
    run never became a question about a boundary, so it exits with the usage
    status and says what it was missing.

    """


def _reported(stderr: str, status: object) -> str:
    """Return the most specific line Git reported for a failed command.

    Parameters
    ----------
    stderr : str
        What the command wrote to standard error.
    status : object
        Exit status the command reported. It is typed loosely because
        GitPython types its exit status as a union wide enough to hold the
        message a command-not-found failure carries, and only its rendering
        matters here.

    Returns
    -------
    str
        The first line Git reported, or the exit status when it reported
        nothing.

    """
    lines = (stderr or "").strip().splitlines()
    if lines:
        return lines[0]
    return f"git exited with status {status}"
