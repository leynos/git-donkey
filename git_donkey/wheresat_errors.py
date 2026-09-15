"""The failures a ``git wheresat`` run can report, and how Git words one.

The graph failures are the ones a read of the repository produces: both halves
of the read-only Git port raise them, and the refusal a shallow clone produces
has to be the same class whichever half asked the question, so the vocabulary
lives below both rather than in the reader that history questions happen to be
asked through. The forge failure is the same kind of thing one layer out — a
question GitHub could not answer — and it is declared here for the same reason,
so that the rung which asks it and the report that renders it can name one
class. The usage failure is the one the command itself produces, and it lives
here because three parts of the command raise it — the resolution that turns
the command line into object IDs, the credential a run needs before it can ask
anything, and the writes a run was asked to make — and the workflow that
reports it must not have to name any of them.

Nothing here reads anything. :func:`failure_line` turns one failed command into
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


class WheresatGitHubError(RuntimeError):
    """A question GitHub could not answer.

    Raised for every way a request can fail to produce an answer — HTTP 401,
    a rate-limited or forbidden 403, 404, any 5xx, a connection timeout, and a
    name that will not resolve. The seven are one class because the run does
    the same thing with all of them: the gate that asked is indeterminate, the
    result is indeterminate, and the exit status is ``3``.

    A 404 is in that list deliberately. GitHub answers a request for a private
    repository's pull request with 404 when the credential cannot see it, so
    reading the status as "there is no such pull request" would turn a
    credentials problem into a confident wrong answer about a boundary — the
    specific failure INV-5 exists to prevent.

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


class WheresatCredentialError(WheresatUsageError):
    """No usable GitHub credential, and no way to obtain one without a terminal.

    ``git fafo`` may stop and ask a human to authorize it, because a human
    asked for it. ``git wheresat`` may not: it is run from scripts and from
    hooks, so a missing credential is never a prompt and never exit ``1``.
    Falling back to the device-flow that ``git fafo`` owns would make a command
    that answers a question about a repository block on a browser.

    What the run reports it as is a question the forge could not answer, so the
    result is ``Indeterminate`` and the status is ``3``: the credential is the
    forge's evidence, and a run that cannot read it cannot tell whether a
    parent pull request exists rather than knowing that none does. The class
    stays a :class:`WheresatUsageError` because the refusal is worded as a
    failure to start, and the ladder that opens the forge names this class
    ahead of its parent so the exit status is the evidence's and not the
    environment's.

    """


def failure_line(stderr: str, status: object) -> str:
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
