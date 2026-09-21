"""The shared record a pull request body may carry.

A local stack record travels with the clone that wrote it and no further. The
same two facts, written in prose into the child's pull request body, travel
wherever the pull request is read:

    Stack parent: owner/repository#123
    Replay boundary (exclusive): <full commit object ID>

This module owns that prose form. It takes a body and says what the body
claims, and it renders a claim back into the form it parses. It is pure, and it
decides nothing about whether a claim is true: what it returns is a candidate
for a run to validate against the repository, never an instruction to it.

Two rules shape the parser. A record is anchored per line, so a body may carry
one inside a longer message with the label bulleted, quoted, or emboldened,
without the decoration becoming part of a value. And a disagreement is never
resolved: a value that does not parse, a record missing one of its two lines,
and a body carrying two records that differ are each reported as they were
found, because a reader that picked one reading would hide the disagreement
from the person who can settle it.

"""

from __future__ import annotations

import dataclasses
import typing as typ

from git_donkey import stack_records

PARENT_LABEL: typ.Final = "Stack parent"
"""The label whose line names the parent pull request."""

BOUNDARY_LABEL: typ.Final = "Replay boundary (exclusive)"
"""The label whose line names the exclusive replay boundary commit."""

_DECORATION: typ.Final = "-*> \t"
"""What a Markdown decoration may put before a label or around a value."""

_FENCES: typ.Final = ("```", "~~~")
"""What a line uses to open and to close a fenced code block."""


@dataclasses.dataclass(frozen=True, slots=True)
class SharedRecord:
    """A stack parent and replay boundary parsed from a pull request body.

    Parameters
    ----------
    parent : stack_records.PullRequestIdentity
        The pull request the body names as the child's stack parent.
    boundary : str
        The full object ID the body names as the exclusive replay boundary.

    """

    parent: stack_records.PullRequestIdentity
    boundary: str


@dataclasses.dataclass(frozen=True, slots=True)
class SharedRecordAbsent:
    """The body carries no stack-parent or replay-boundary line."""


@dataclasses.dataclass(frozen=True, slots=True)
class SharedRecordMalformed:
    """A line matched but the record did not hold together, and why.

    Parameters
    ----------
    reason : str
        One reason per line that could not be read, in the order the body
        gives them, joined by ``"; "``. Every fault is reported rather than
        the first, because a record is repaired by a person and a person who
        is shown one fault at a time is sent back for the next one.

    """

    reason: str


@dataclasses.dataclass(frozen=True, slots=True)
class SharedRecordAmbiguous:
    """Two or more disagreeing occurrences; never silently resolved.

    Parameters
    ----------
    records : tuple[SharedRecord, ...]
        Every complete record the body supports: one reading per distinct
        stack parent and distinct replay boundary it names, each in the order
        the body gives them. A body naming two parents and one boundary
        supports two readings, and both are here, because choosing either is
        the resolution this result exists to refuse.

    """

    records: tuple[SharedRecord, ...]


type SharedRecordResult = (
    SharedRecord | SharedRecordAbsent | SharedRecordMalformed | SharedRecordAmbiguous
)


def parse_shared_record(body: str) -> SharedRecordResult:
    """Extract a stack parent and replay boundary from a pull request body.

    The grammar is anchored per line and tolerates leading ``-``, ``*``, ``>``,
    and ``**`` decoration, so a bulleted, quoted, or emboldened record is still
    read: a quoted record is a claim like any other, and it is a candidate the
    run then validates rather than an instruction it obeys. Fenced code blocks
    are skipped instead. Text inside a fence is quoted material — a sample of
    the form, a diff of a record, instructions copied from a guide — and not
    the claim the body's author is making.

    Object IDs must be full 40 or 64 hexadecimal characters; abbreviations are
    rejected rather than resolved. Exactly one occurrence of each field is
    required and the values must agree: two disagreeing occurrences yield
    ``SharedRecordAmbiguous`` rather than a silent choice, and a record naming
    only one of its two lines is malformed rather than absent.

    Parameters
    ----------
    body : str
        A pull request body, or any other prose written in the same form.

    Returns
    -------
    SharedRecordResult
        The record the body carries, or why there is none to carry.

    """
    parents, boundaries, faults = _claims(body)
    if faults:
        return SharedRecordMalformed(reason="; ".join(faults))
    return _identified(parents, boundaries)


def render_shared_record(record: SharedRecord) -> str:
    """Render a shared-record block for pasting into a pull request body.

    The two lines are written as a body's author writes them, plain and with
    the labels this module parses. ``parse_shared_record(render_shared_record(r))``
    must return ``r``; this round-trip is a property test, and it is why the
    renderer exists even though no command emits it. The users' guide presents
    its output as copy-paste text.

    Parameters
    ----------
    record : SharedRecord
        The record to render.

    Returns
    -------
    str
        The two lines, with no trailing newline.

    Raises
    ------
    ValueError
        If the record is not one a body could have carried. A repository with
        a newline in it, or a boundary that is not a full object ID, would
        render to text that parses as some other record, and writing text that
        means another thing is the one outcome this module must not produce.
        The check is the round trip itself rather than a second copy of the
        grammar, so a form this parser would refuse is refused here too.

    """
    text = (
        f"{PARENT_LABEL}: {stack_records.identity_text(record.parent)}\n"
        f"{BOUNDARY_LABEL}: {record.boundary}"
    )
    if parse_shared_record(text) != record:
        msg = f"the shared record cannot be rendered as a body: {record!r}"
        raise ValueError(msg)
    return text


def _claims(
    body: str,
) -> tuple[list[stack_records.PullRequestIdentity], list[str], list[str]]:
    """Return the values the body carries and the lines that did not read.

    A label is claimed only where a line starts with it, once the line's
    decoration is removed, and its value runs to the end of that line: a line
    naming one label therefore carries that label's value and gives no value to
    any other label it also names. A line that claims none of the labels is not
    a line that failed.

    Parameters
    ----------
    body : str
        Prose that may carry a shared record.

    Returns
    -------
    tuple[list[stack_records.PullRequestIdentity], list[str], list[str]]
        The parents named, the boundaries named, and one reason per line that
        claimed a label without giving a value the grammar accepts. All three
        are in the order the body gives them.

    """
    parents: list[stack_records.PullRequestIdentity] = []
    boundaries: list[str] = []
    faults: list[str] = []
    for text in _content(body):
        _read_parent(text, parents, faults)
        _read_boundary(text, boundaries, faults)
    return parents, boundaries, faults


def _read_parent(
    text: str,
    parents: list[stack_records.PullRequestIdentity],
    faults: list[str],
) -> None:
    """Add what one line claims about the parent, or why it could not be read.

    Parameters
    ----------
    text : str
        One line of the body, with its leading whitespace removed.
    parents : list[stack_records.PullRequestIdentity]
        The parents read so far, which this line's claim is appended to.
    faults : list[str]
        The faults read so far, which this line's fault is appended to.

    """
    value = _value(text, PARENT_LABEL)
    if value is None:
        return
    identity = stack_records.parse_pull_request_identity(value)
    if identity is None:
        faults.append(f"the shared record has an unreadable parent value {value!r}")
        return
    parents.append(identity)


def _read_boundary(text: str, boundaries: list[str], faults: list[str]) -> None:
    """Add what one line claims about the boundary, or why it could not be read.

    Parameters
    ----------
    text : str
        One line of the body, with its leading whitespace removed.
    boundaries : list[str]
        The boundaries read so far, which this line's claim is appended to.
    faults : list[str]
        The faults read so far, which this line's fault is appended to.

    """
    value = _value(text, BOUNDARY_LABEL)
    if value is None:
        return
    if stack_records.is_object_id(value):
        boundaries.append(value)
        return
    faults.append(
        f"the shared record names a boundary that is not a full object ID: {value!r}"
    )


def _identified(
    parents: list[stack_records.PullRequestIdentity],
    boundaries: list[str],
) -> SharedRecordResult:
    """Return the record two sets of claims name, or why they name none.

    A body that claimed neither line carries no record; one that claimed only
    one of the two was written as a record and did not hold together, which is
    a fault rather than an absence. Every parent the body names is read with
    every boundary it names, and one reading is the answer while several are
    the disagreement this result refuses to settle.

    Parameters
    ----------
    parents : list[stack_records.PullRequestIdentity]
        The parents the body names, in the order it gives them.
    boundaries : list[str]
        The boundaries the body names, in the order it gives them.

    Returns
    -------
    SharedRecordResult
        The one record the claims agree on, the absence of any claim, the
        missing line that stopped the body from holding together, or every
        reading it supports.

    """
    if not parents and not boundaries:
        return SharedRecordAbsent()
    missing = _missing_line(parents, boundaries)
    if missing is not None:
        return missing
    readings = _readings(parents, boundaries)
    if len(readings) == 1:
        return readings[0]
    return SharedRecordAmbiguous(records=readings)


def _missing_line(
    parents: list[stack_records.PullRequestIdentity],
    boundaries: list[str],
) -> SharedRecordMalformed | None:
    """Return the fault naming the line a body wrote only one of, if it did.

    The label named is the one the body did not write, because that is the line
    its author has to add.

    Parameters
    ----------
    parents : list[stack_records.PullRequestIdentity]
        The parents the body names, in the order it gives them.
    boundaries : list[str]
        The boundaries the body names, in the order it gives them.

    Returns
    -------
    SharedRecordMalformed | None
        The fault naming the line that is missing, or ``None`` when the body
        named both.

    """
    if not parents:
        reason = f"the shared record is missing its {PARENT_LABEL} line"
        return SharedRecordMalformed(reason=reason)
    if not boundaries:
        reason = f"the shared record is missing its {BOUNDARY_LABEL} line"
        return SharedRecordMalformed(reason=reason)
    return None


def _readings(
    parents: list[stack_records.PullRequestIdentity],
    boundaries: list[str],
) -> tuple[SharedRecord, ...]:
    """Return one reading per distinct parent and distinct boundary.

    Parameters
    ----------
    parents : list[stack_records.PullRequestIdentity]
        The parents the body names, in the order it gives them.
    boundaries : list[str]
        The boundaries the body names, in the order it gives them.

    Returns
    -------
    tuple[SharedRecord, ...]
        Every pairing of a parent with a boundary, each parent and each
        boundary taken once however often the body repeated it.

    """
    return tuple(
        SharedRecord(parent=parent, boundary=boundary)
        for parent in dict.fromkeys(parents)
        for boundary in dict.fromkeys(boundaries)
    )


def _value(text: str, label: str) -> str | None:
    """Return the value a line gives ``label``, or ``None`` when it gives none.

    Parameters
    ----------
    text : str
        One line of the body, with its leading whitespace removed. Markdown
        decoration is removed here rather than before, so a bulleted, quoted,
        or emboldened label is still read as the label it spells.
    label : str
        The label a line claims by starting with it.

    Returns
    -------
    str | None
        The value after the label's colon, with the decoration around it
        removed, or ``None`` when the line does not claim the label at all.

    """
    text = text.lstrip(_DECORATION)
    if not text.startswith(label):
        return None
    remainder = text[len(label) :].strip().lstrip("*").strip()
    if not remainder.startswith(":"):
        return None
    return remainder[1:].strip().strip("*").strip()


def _content(body: str) -> list[str]:
    """Return the body's lines, with fenced code blocks left out.

    Parameters
    ----------
    body : str
        The prose to read.

    Returns
    -------
    list[str]
        One stripped line per line outside a fence, in the order the body
        gives them. A fence opens and closes on a line that begins with three
        backticks or three tildes, and a fence left open quotes the rest of
        the body rather than opening it.

    """
    lines: list[str] = []
    fenced = False
    for line in body.splitlines():
        text = line.strip()
        if text.startswith(_FENCES):
            fenced = not fenced
        elif not fenced:
            lines.append(text)
    return lines
