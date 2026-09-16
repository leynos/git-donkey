"""Unit tests for the shared record a pull request body may carry.

The record is read by a run that was never in the clone that wrote it, so it
has to survive being pasted into a pull request body — where it will be
bulleted, quoted, or emboldened, and where it may sit next to a sample of the
form that looks exactly like it. These tests pin what the parser reads, what it
refuses to resolve, and the round trip that ties the parser to the renderer.
Everything here is pure: no repository, no Git, and no forge.
"""

from __future__ import annotations

import string

import pytest
from hypothesis import given
from hypothesis import strategies as st

from git_donkey import stack_records
from git_donkey.wheresat_shared_record import (
    BOUNDARY_LABEL,
    PARENT_LABEL,
    SharedRecord,
    SharedRecordAbsent,
    SharedRecordAmbiguous,
    SharedRecordMalformed,
    parse_shared_record,
    render_shared_record,
)

_PARENT = stack_records.PullRequestIdentity(repository="octocat/demo-repo", number=7)
_OTHER_PARENT = stack_records.PullRequestIdentity(repository="octocat/other", number=8)
_BOUNDARY = "a" * 40
_OTHER_BOUNDARY = "b" * 64

_SLUG = r"[A-Za-z0-9][A-Za-z0-9._-]{0,15}"
_IDENTITIES = st.builds(
    stack_records.PullRequestIdentity,
    repository=st.from_regex(f"{_SLUG}/{_SLUG}", fullmatch=True),
    number=st.integers(min_value=1, max_value=10**6),
)
_OBJECT_IDS = st.one_of(
    st.text(alphabet=string.hexdigits, min_size=40, max_size=40),
    st.text(alphabet=string.hexdigits, min_size=64, max_size=64),
)
_RECORDS = st.builds(SharedRecord, parent=_IDENTITIES, boundary=_OBJECT_IDS)


def _block(
    parent: str = stack_records.identity_text(_PARENT),
    boundary: str = _BOUNDARY,
) -> str:
    """Return a body carrying both lines of a shared record.

    Parameters
    ----------
    parent : str
        The parent value the block names, as a body spells it.
    boundary : str
        The boundary value the block names, as a body spells it.

    Returns
    -------
    str
        The two lines a body would carry, with no decoration around them and
        no trailing newline.

    """
    return f"{PARENT_LABEL}: {parent}\n{BOUNDARY_LABEL}: {boundary}"


def test_both_lines_are_the_record() -> None:
    """A body carrying both lines parses into the record they name."""
    assert parse_shared_record(_block()) == SharedRecord(
        parent=_PARENT, boundary=_BOUNDARY
    ), "the two lines are the whole grammar"


@pytest.mark.parametrize(
    "decoration",
    ["- ", "* ", "> ", "**", "  "],
    ids=["bulleted", "starred", "quoted", "emboldened", "indented"],
)
def test_decoration_around_a_line_is_not_part_of_the_record(decoration: str) -> None:
    """A body may embolden or bullet the record without changing it."""
    body = "\n".join(f"{decoration}{line}" for line in _block().splitlines())

    assert parse_shared_record(body) == SharedRecord(
        parent=_PARENT, boundary=_BOUNDARY
    ), "the decoration is not part of either value"


def test_a_record_between_other_lines_is_still_read() -> None:
    """The grammar is anchored per line, so the record need not stand alone."""
    body = (
        "This branch was branched off its parent.\n\n"
        f"{_block()}\n\n"
        "Replaying from the boundary gives the same tree.\n"
    )

    assert parse_shared_record(body) == SharedRecord(
        parent=_PARENT, boundary=_BOUNDARY
    ), "the two lines are found among whatever else the body says"


def test_a_fence_quotes_what_is_inside_it() -> None:
    """A sample inside a fenced block is quoted, not claimed."""
    body = (
        f"{_block()}\n\n"
        "The form is:\n\n"
        "```plaintext\n"
        f"{_block(boundary=_OTHER_BOUNDARY)}\n"
        "```\n"
    )

    assert parse_shared_record(body) == SharedRecord(
        parent=_PARENT, boundary=_BOUNDARY
    ), "only the lines outside a fence are the author's own claim"


def test_a_body_with_no_record_at_all_is_absent() -> None:
    """Prose that never claims a label leaves the body without a record."""
    body = "A branch created from the trunk is not recorded, whatever it is called."

    assert parse_shared_record(body) == SharedRecordAbsent(), (
        "nothing claimed a label, so there is no record to report"
    )


def test_a_line_that_misses_the_spelling_is_not_a_claim() -> None:
    """The labels are the whole form; a line that misses one claims nothing."""
    body = (
        f"stack parent: octocat/demo-repo#7\nreplay boundary (exclusive): {_BOUNDARY}"
    )

    assert parse_shared_record(body) == SharedRecordAbsent(), (
        "a line the labels do not anchor is prose, not a record"
    )


@pytest.mark.parametrize(
    ("body", "missing"),
    [
        (_block().splitlines()[0], BOUNDARY_LABEL),
        (_block().splitlines()[1], PARENT_LABEL),
    ],
    ids=["no-boundary", "no-parent"],
)
def test_a_record_naming_one_line_only_is_malformed(body: str, missing: str) -> None:
    """Half a record is reported rather than read as an absence."""
    result = parse_shared_record(body)

    assert isinstance(result, SharedRecordMalformed), (
        "a body that names one line claims a record it did not finish"
    )
    assert missing in result.reason, (
        f"the reason names the {missing} line it could not find; it says "
        f"{result.reason!r}"
    )


@pytest.mark.parametrize(
    ("parent", "boundary"),
    [
        ("octocat/demo-repo", _BOUNDARY),
        ("octocat/demo-repo#0", _BOUNDARY),
        ("octocat/demo-repo#seven", _BOUNDARY),
        ("demo-repo#7", _BOUNDARY),
        ("", _BOUNDARY),
        ("octocat/demo-repo#7", _BOUNDARY[:39]),
        ("octocat/demo-repo#7", "z" * 40),
        ("octocat/demo-repo#7", ""),
    ],
    ids=[
        "no-number",
        "zero-number",
        "spelled-number",
        "no-owner",
        "empty-parent",
        "abbreviated-object-id",
        "not-hexadecimal",
        "empty-boundary",
    ],
)
def test_a_value_the_grammar_refuses_is_malformed(parent: str, boundary: str) -> None:
    """A value outside the grammar is reported, never resolved to a nearby one."""
    result = parse_shared_record(_block(parent=parent, boundary=boundary))

    assert isinstance(result, SharedRecordMalformed), (
        "a claimed value that does not parse is a fault, not an absence"
    )


def test_two_agreeing_lines_are_one_claim() -> None:
    """A body that says the same thing twice says one thing."""
    assert parse_shared_record(f"{_block()}\n\n{_block()}") == SharedRecord(
        parent=_PARENT, boundary=_BOUNDARY
    ), "agreement is not ambiguity"


def test_two_disagreeing_parents_are_ambiguous() -> None:
    """Two parents leave two complete readings, and neither is chosen."""
    body = (
        _block(parent="octocat/demo-repo#7") + "\n" + _block(parent="octocat/other#8")
    )

    assert parse_shared_record(body) == SharedRecordAmbiguous(
        records=(
            SharedRecord(parent=_PARENT, boundary=_BOUNDARY),
            SharedRecord(parent=_OTHER_PARENT, boundary=_BOUNDARY),
        )
    ), "each reading the body supports is reported, in the order it is read"


def test_two_disagreeing_boundaries_are_ambiguous() -> None:
    """Two boundaries leave two complete readings, and neither is chosen."""
    body = _block(boundary=_BOUNDARY) + "\n" + _block(boundary=_OTHER_BOUNDARY)

    assert parse_shared_record(body) == SharedRecordAmbiguous(
        records=(
            SharedRecord(parent=_PARENT, boundary=_BOUNDARY),
            SharedRecord(parent=_PARENT, boundary=_OTHER_BOUNDARY),
        )
    ), "a disagreement about the boundary is reported like one about the parent"


def test_the_rendered_block_is_the_text_a_body_may_carry() -> None:
    """The users' guide quotes this block, so its spelling is pinned here."""
    rendered = render_shared_record(SharedRecord(parent=_PARENT, boundary=_BOUNDARY))

    assert rendered == (
        "Stack parent: octocat/demo-repo#7\nReplay boundary (exclusive): " + "a" * 40
    ), "the rendered block is the two lines the guide tells a reader to paste"


@pytest.mark.parametrize(
    ("parent", "boundary"),
    [
        (_PARENT, "not-an-object-id"),
        (_PARENT, _BOUNDARY[:39]),
        (
            stack_records.PullRequestIdentity(
                repository="octocat/demo\nrepo", number=7
            ),
            _BOUNDARY,
        ),
    ],
    ids=["not-an-object-id", "abbreviated-object-id", "parent-over-two-lines"],
)
def test_rendering_refuses_a_record_no_body_could_carry(
    parent: stack_records.PullRequestIdentity, boundary: str
) -> None:
    """A record whose text would parse as another one is refused, not written."""
    record = SharedRecord(parent=parent, boundary=boundary)

    # Rendering is the parser's inverse, so a record whose text would parse as
    # a different record is one this module must not write.
    with pytest.raises(ValueError, match="cannot be rendered"):
        render_shared_record(record)


@given(record=_RECORDS)
def test_a_rendered_record_parses_back_to_itself(record: SharedRecord) -> None:
    """The round trip is the renderer's contract, so it holds for every record."""
    assert parse_shared_record(render_shared_record(record)) == record, (
        "a record written for a body is the record read back from it"
    )
