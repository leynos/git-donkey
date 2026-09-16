"""Reading GitHub's decoded bodies into the vocabulary the command speaks.

Every field read here was written by somebody else. An ``int`` may arrive where
a flag was expected, a list where an object was, and a field an endpoint
documents may simply be missing, so each reader answers one narrow question
about one value and says what it does when the answer is no. The distinction it
keeps is the one the whole feature turns on: a field that is absent is nothing,
and a body that is not the shape its endpoint promises is a question that went
unanswered. That is why the lenient and strict readers are separate functions
rather than one tolerant helper — an omitted list field and a body that is not
a list at all look alike to a reader that does not care which it saw, and only
one of them means the run cannot tell.

These readers are pure: they take a decoded body and return a value, with no
session, no URL, and no request. They live apart from ``wheresat_github`` for
that reason, so the module that speaks HTTP is about requests and their faults,
and this one is about the shape of what came back.

"""

from __future__ import annotations

import typing as typ

from git_donkey import stack_records
from git_donkey.wheresat_errors import WheresatGitHubError

if typ.TYPE_CHECKING:
    import collections.abc as cabc


def mapping(payload: object) -> cabc.Mapping[str, object]:
    """Return ``payload`` as a mapping, or report that it is not one.

    Parameters
    ----------
    payload : object
        Decoded body of an endpoint that answers with an object.

    Returns
    -------
    collections.abc.Mapping[str, object]
        The body's fields.

    Raises
    ------
    WheresatGitHubError
        If GitHub answered a question it does not answer that way. A body this
        version cannot read is a question that went unanswered, not an absence
        of the thing asked for.

    """
    if isinstance(payload, dict):
        return typ.cast("cabc.Mapping[str, object]", payload)
    msg = "GitHub answered with a body this version does not understand"
    raise WheresatGitHubError(msg)


def nested(payload: object, *keys: str) -> object:
    """Return what ``keys`` names inside ``payload``, or ``None``.

    The payload is somebody else's data, so a missing field and a field of the
    wrong shape are answered the same way — as nothing — rather than raised
    over: what GitHub omitted is not this command's fault, and a helper that
    raised would turn every optional field into a fault.

    Parameters
    ----------
    payload : object
        Decoded body to read.
    *keys : str
        Field names, outermost first.

    Returns
    -------
    object
        The value, or ``None`` when any step of the walk is absent.

    """
    current = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def sequence(payload: object, what: str) -> list[object]:
    """Return ``payload`` as a list, or report that it is not one.

    Parameters
    ----------
    payload : object
        Decoded body of an endpoint that answers with a list.
    what : str
        How the expected shape is described in the refusal.

    Returns
    -------
    list[object]
        The body's entries.

    Raises
    ------
    WheresatGitHubError
        If the body is not a list, which is a question this version cannot
        read the answer to rather than an answer of none.

    """
    if isinstance(payload, list):
        return payload
    msg = f"GitHub answered with {what} this version does not understand"
    raise WheresatGitHubError(msg)


def list_field(value: object) -> list[object]:
    """Return ``value`` as a list, or no items when it is not one.

    For a field of a payload this version already understands, where an
    omitted list means the same as an empty one — the members of a stack that
    reports none, say. A body whose *own* shape is a list goes through
    :func:`sequence`, because there an empty list is an answer and a
    non-list is a fault, and the two must not be confused.

    Parameters
    ----------
    value : object
        Field to read.

    Returns
    -------
    list[object]
        The field's entries, or none when the field is not a list.

    """
    return value if isinstance(value, list) else []


def string_field(value: object) -> str:
    """Return ``value`` when it is a string, and the empty string otherwise.

    A field this version reads as text and the forge sent as something else is
    read as absent rather than rendered: the empty string is what a caller
    tests for having nothing, so a number or a nested object cannot reach the
    report dressed as a name.

    Parameters
    ----------
    value : object
        Field to read.

    Returns
    -------
    str
        The field's text, or the empty string when the field is not a string.

    """
    return value if isinstance(value, str) else ""


def flag_field(value: object) -> bool:
    """Return ``value`` when it is a boolean, and false otherwise.

    ``True`` and the int ``1`` are not the same answer from an API that sends
    JSON, so an int is read as no flag rather than as a yes.

    Parameters
    ----------
    value : object
        Field to read.

    Returns
    -------
    bool
        The flag, or false when the field is not one.

    """
    return value if isinstance(value, bool) else False


def count_field(value: object) -> int | None:
    """Return ``value`` when it is a count, and ``None`` otherwise.

    A boolean is rejected although it *is* an int in Python, because JSON has
    a boolean of its own: a forge that answered ``true`` where a count was
    read has answered a flag, and reading it as ``1`` would put a number in
    the report that no endpoint sent.

    Parameters
    ----------
    value : object
        Field to read.

    Returns
    -------
    int | None
        The count, or ``None`` when the field is not one.

    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def pull_identity(
    repository: str, member: object
) -> stack_records.PullRequestIdentity | None:
    """Return the pull request a stack member names, or ``None`` when it names none.

    Parameters
    ----------
    repository : str
        Slug every member of the stack belongs to, which GitHub requires them
        to share, so a member carries no repository of its own.
    member : object
        One entry of a stack's ``pull_requests`` list.

    Returns
    -------
    stack_records.PullRequestIdentity | None
        The identity, or ``None`` when the entry names no pull request.

    """
    number = count_field(nested(member, "number"))
    if number is None:
        return None
    return stack_records.PullRequestIdentity(repository=repository, number=number)


def associated(
    payload: object, repository: str
) -> tuple[stack_records.PullRequestIdentity, ...]:
    """Return the pull requests one commit was associated with.

    Parameters
    ----------
    payload : object
        Decoded body of the association endpoint, which lists pull requests.
    repository : str
        Slug the association was asked about.

    Returns
    -------
    tuple[stack_records.PullRequestIdentity, ...]
        One identity per pull request GitHub named, in its own order.

    Raises
    ------
    WheresatGitHubError
        If GitHub answered a question it does not answer that way. A body that
        is not the list its endpoint promises is a question that went
        unanswered, not an absence of pull requests.

    """
    identities = (
        pull_identity(repository, entry)
        for entry in sequence(payload, "a list of pull requests")
        if isinstance(entry, dict)
    )
    return tuple(identity for identity in identities if identity is not None)
