"""The shared stack record's format and its pure decisions.

Three commands agree about one artefact. ``git donkey`` writes a record when it
creates a branch from a base that is not the trunk, ``git wheresat`` reads it as
its strongest boundary evidence and refreshes it, and ``git plonk`` converts it
into a tombstone before deleting the branch and sweeps the records whose branch
has gone. This module owns the format and the decisions; it contains no
GitPython, filesystem, network, or process access, and imports nothing else
from ``git_donkey``, so every command can depend on it without depending on the
others.

A record is split across two artefacts with different lifetimes. The four
configuration keys in the branch's own section hold the *values*, and the
anchor ref ``refs/stack-bases/<branch>`` holds the same boundary commit so
``git gc`` cannot collect the object a surviving child depends on. Which
artefact is authoritative is forced by measurement rather than chosen for
symmetry: ``git branch -m`` carries the configuration section to the new name
and leaves the anchor behind, while ``git branch -D`` destroys the section
entirely. So configuration is authoritative for values, the anchor only for
reachability, and the one case where they disagree is reported rather than
resolved.

See ``docs/stack-records.md`` for the contract these functions implement, and
``docs/adr-004-shared-stack-records.md`` for why it is one shared record rather
than a private format per command.
"""

from __future__ import annotations

import dataclasses
import enum
import re
import typing as typ

RECORD_VERSION: typ.Final = "v1"
"""Version prefix on every value, so a later revision can extend the grammar."""

BASE_NAMESPACE: typ.Final = "refs/stack-bases"
"""Ref namespace that keeps each record's boundary commit reachable."""

TOMBSTONE_NAMESPACE: typ.Final = "refs/stack-tombstones"
"""Ref namespace that preserves the tip of a deleted branch."""

EVIDENCE_BIRTH: typ.Final = "stack-record-birth"
"""Evidence kind written when the record is created at branch birth."""

EVIDENCE_REFRESHED: typ.Final = "stack-record-refreshed"
"""Evidence kind written when ``git wheresat --record`` moves a record.

The value is what distinguishes a record that still says what was true at
branch birth from one a later run deliberately re-stated, and it is read back
as attested evidence either way: both are statements someone made on purpose.
"""

DEFAULT_TOMBSTONE_EXPIRE: typ.Final = "90.days.ago"
"""Retention window used when ``stack.tombstoneExpire`` is unset.

The same horizon as Git's own ``gc.reflogExpire``, so a tombstone lasts exactly
as long as the reflog it stands in for would have.
"""

_GITHUB_HOST: typ.Final = "github.com"
"""Host a remote URL must name for its path to be read as a repository slug."""

_GIT_SUFFIX: typ.Final = ".git"
"""Suffix a remote URL may carry on the repository name, and which is not one."""


class RecordKey(enum.StrEnum):
    """The per-branch configuration keys, without the ``branch.<name>.`` prefix.

    Git returns configuration variable names in lower case, so every lookup is
    case-insensitive and these are the canonical lower-case spellings.
    """

    PARENT = "stackparent"
    BASE = "stackbase"
    RECORDED_FROM = "stackbaserecordedfrom"
    EVIDENCE = "stackbaseevidence"


@dataclasses.dataclass(frozen=True, slots=True)
class PullRequestIdentity:
    """A pull request named by repository and number.

    Defined here rather than in ``wheresat_records`` because ``StackParent``
    needs it and this module must not depend on anything above it.

    Parameters
    ----------
    repository : str
        Fully qualified ``owner/repository`` slug, not a bare name.
    number : int
        Pull request number within that repository.

    """

    repository: str
    number: int


@dataclasses.dataclass(frozen=True, slots=True)
class StackParent:
    """Who a branch is stacked on: a branch at birth, a pull request later.

    Exactly one field is populated for any parent the parser produces.

    Parameters
    ----------
    branch : str | None
        Parent branch name, for a record written at birth.
    pull_request : PullRequestIdentity | None
        Parent pull request, for a record written after the parent was opened.

    """

    branch: str | None
    pull_request: PullRequestIdentity | None


@dataclasses.dataclass(frozen=True, slots=True)
class StackRecord:
    """One branch's stack record, as stored.

    Parameters
    ----------
    branch : str
        Branch the record was read for.
    parent : StackParent
        Who the branch is stacked on.
    base : str
        The exclusive replay boundary, a full object ID.
    recorded_from : str
        The child tip observed when the record was written.
    evidence : str
        The evidence kind that established the boundary.

    """

    branch: str
    parent: StackParent
    base: str
    recorded_from: str
    evidence: str


@dataclasses.dataclass(frozen=True, slots=True)
class RecordAbsent:
    """The branch has no record. A first-class state, never an error."""


@dataclasses.dataclass(frozen=True, slots=True)
class RecordMalformed:
    """A half-record, a disagreeing anchor, or an unknown version.

    Parameters
    ----------
    reason : str
        Why the artefacts could not be reconciled, for the report.

    """

    reason: str


@dataclasses.dataclass(frozen=True, slots=True)
class RecordOrphaned:
    """A record whose branch no longer exists; never used as evidence.

    Parameters
    ----------
    branch : str
        The branch that has gone, leaving its record behind.

    """

    branch: str


type RecordResult = StackRecord | RecordAbsent | RecordMalformed | RecordOrphaned

# Spelled with an explicit ASCII range rather than with re.IGNORECASE, because
# a case-insensitive character class also accepts the four non-ASCII letters
# Unicode case folding adds to it, and an object ID is hexadecimal in ASCII.
_OBJECT_ID_PATTERN = re.compile(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}")

# Each rule is a pattern and the reason it rejects a name. A table rather than
# a chain of conditions keeps the reasons next to the rules and the function
# that applies them free of branching. The value may be hierarchical — a branch
# may be named ``feature/child`` — so the rules are Git's own rules for a ref
# name below the namespace this module prefixes, not the stricter rules for a
# single path component.
_COMPONENT_RULES: typ.Final = (
    (re.compile(r"^-"), "it begins with '-', which is argument injection"),
    (re.compile(r"^/|/$"), "it begins or ends with '/'"),
    (re.compile(r"//"), "it holds an empty path component"),
    (re.compile(r"\.\."), "it holds '..'"),
    (re.compile(r"@\{"), "it holds '@{'"),
    (re.compile(r"(?:^|/)[.]"), "a path component may not begin with '.'"),
    (re.compile(r"[.]lock(?:/|$)"), "a path component ends with '.lock'"),
    (re.compile(r"[.](?:/|$)"), "a path component ends with '.'"),
    (re.compile(r"[\x00-\x20\x7f]"), "it holds a control character or a space"),
    (re.compile(r"[~^:?*\[\\]"), "it holds a character git refuses in a ref"),
)


def _component_rejection(value: str) -> str | None:
    """Return why ``value`` cannot be a ref path component, or ``None``."""
    if not value:
        return "it is empty"
    for pattern, reason in _COMPONENT_RULES:
        if pattern.search(value) is not None:
            return reason
    return None


def validate_ref_component(value: str) -> str:
    """Return ``value`` when it is safe under a ref namespace, else raise.

    Rejects a leading ``-``, an embedded ``:``, ``..``, control characters, and
    anything ``git check-ref-format`` would refuse. A hierarchical name such as
    ``feature/child`` is accepted, because a branch may be named that and the
    record's refs mirror the branch's own name; an empty path component, a
    component beginning with ``.``, and a component ending in ``.`` or
    ``.lock`` are refused, as Git refuses them. ``git wheresat`` reuses this
    for ``--op-id``, because that value reaches a fetch refspec destination
    where ``:`` is the separator and a leading ``-`` is argument injection.
    That last rule is the one deliberate divergence from Git, which accepts a
    leading ``-`` in a ref name; the danger is in the argv, not the ref store.

    Parameters
    ----------
    value : str
        Candidate name, typically a branch.

    Returns
    -------
    str
        The value, unchanged.

    Raises
    ------
    ValueError
        If the value would be unsafe in a ref path.

    """
    reason = _component_rejection(value)
    if reason is not None:
        msg = f"invalid ref path component {value!r}: {reason}"
        raise ValueError(msg)
    return value


def _ref_path(namespace: str, branch: str) -> str:
    """Return ``namespace``'s ref for ``branch``, validating the branch name.

    Parameters
    ----------
    namespace : str
        Ref namespace the branch's record lives in.
    branch : str
        Branch the ref is wanted for.

    Returns
    -------
    str
        The ref path.

    Raises
    ------
    ValueError
        If the branch name would be unsafe in a ref path.

    """
    return f"{namespace}/{validate_ref_component(branch)}"


def base_ref_path(branch: str) -> str:
    """Return ``refs/stack-bases/<branch>``, validating the branch name.

    Parameters
    ----------
    branch : str
        Branch whose anchor ref is wanted.

    Returns
    -------
    str
        The anchor ref path.

    Raises
    ------
    ValueError
        If the branch name would be unsafe in a ref path.

    """
    return _ref_path(BASE_NAMESPACE, branch)


def tombstone_ref_path(branch: str) -> str:
    """Return ``refs/stack-tombstones/<branch>``, validating the branch name.

    A tombstone is what a branch leaves behind when it is deleted: the anchor
    it was born at, still resolvable under a ref that outlives it. It is a
    separate namespace from the anchor rather than a second value inside it,
    so that a sweep can find every branch that has gone by listing one
    namespace, and so that the tip a deleted branch stood on stays reachable
    for ``git wheresat`` long after the branch itself is gone.

    Parameters
    ----------
    branch : str
        Branch whose tombstone ref is wanted.

    Returns
    -------
    str
        The tombstone ref path.

    Raises
    ------
    ValueError
        If the branch name would be unsafe in a ref path.

    """
    return _ref_path(TOMBSTONE_NAMESPACE, branch)


def _is_positive_integer(text: str) -> bool:
    """Return whether ``text`` is a decimal count of at least one."""
    return text.isdigit() and int(text) >= 1


def is_repository_slug(owner: str, separator: str, name: str) -> bool:
    """Return whether the three parts of ``owner/name`` form a slug.

    Parameters
    ----------
    owner : str
        Text before the separator, from ``str.partition("/")``.
    separator : str
        The separator ``str.partition`` returned, empty when it found none.
    name : str
        Text after the separator.

    Returns
    -------
    bool
        Whether the three are one owner, one separator, and one repository
        name, with no further separator in the name. Public because the
        command that builds a URL out of a slug must refuse a value this
        rejects, and a second copy of the rule would be a second opinion about
        what a slug is.

    """
    if not separator or not owner:
        return False
    return bool(name) and "/" not in name


def repository_from_remote_url(url: str) -> str | None:
    """Return the ``owner/name`` a remote URL names, when it names one here.

    Git writes a GitHub remote URL in one of three spellings —
    ``https://github.com/owner/name``, ``git@github.com:owner/name``, and
    ``ssh://git@github.com/owner/name`` — and any of them may end in ``.git``.
    All three are read, because a clone's spelling is the user's choice rather
    than the tool's, and the answer is the slug alone: the scheme, the user,
    and the port say nothing about which repository the remote holds.

    An answer of ``None`` is not a failure to parse. It is the refusal to
    guess, because the caller is looking for the one remote that holds a
    particular repository: a URL naming another host, or a path that is not an
    ``owner/name`` pair, must not be read as a near miss for the repository it
    was compared against.

    Parameters
    ----------
    url : str
        URL a remote is configured with, as Git stores it.

    Returns
    -------
    str | None
        The ``owner/name`` the URL names on GitHub, or ``None`` when it names
        no repository there.

    """
    parts = _url_host_and_path(url)
    if parts is None:
        return None
    host, path = parts
    if host != _GITHUB_HOST:
        return None
    slug = path.removesuffix(_GIT_SUFFIX)
    owner, slash, name = slug.partition("/")
    if not is_repository_slug(owner, slash, name):
        return None
    return slug


def _url_host_and_path(url: str) -> tuple[str, str] | None:
    """Return the host and path a remote URL names, when it names both.

    Both shapes Git writes are read here and told apart by separator: a URL
    proper has a scheme, so its host follows ``://`` and its path follows the
    first slash after it, while the scp-like form has no scheme and puts its
    host after the ``@`` and its path after the colon. Anything else — a local
    path, a bundle, a file URL — is answered with ``None`` rather than read as
    whichever shape it most resembles.

    Parameters
    ----------
    url : str
        URL a remote is configured with.

    Returns
    -------
    tuple[str, str] | None
        The host and the path, or ``None`` when the URL names neither.

    """
    if "://" in url:
        _, _, remainder = url.partition("://")
        host, _, path = remainder.partition("/")
        host = host.rpartition("@")[2].partition(":")[0]
        return (host, path) if host and path else None
    if "@" in url and ":" in url:
        _, _, remainder = url.partition("@")
        host, _, path = remainder.partition(":")
        return (host, path) if host and path else None
    return None


def parse_pull_request_identity(text: str) -> PullRequestIdentity | None:
    """Parse ``owner/repository#123``, returning ``None`` when unrecognized.

    Parameters
    ----------
    text : str
        Candidate identity, without a version prefix.

    Returns
    -------
    PullRequestIdentity | None
        The parsed identity, or ``None`` when the text is not one.

    """
    repository, separator, number = text.partition("#")
    if not separator or not _is_positive_integer(number):
        return None
    owner, slash, name = repository.partition("/")
    if not is_repository_slug(owner, slash, name):
        return None
    return PullRequestIdentity(repository=repository, number=int(number))


def parse_parent(value: str) -> StackParent | None:
    """Parse ``v1:branch:<name>`` or ``v1:pr:<owner>/<repo>#<n>``.

    The version prefix is what lets a later revision add a field without a
    reader from this revision misinterpreting it.

    Parameters
    ----------
    value : str
        The stored value of ``branch.<name>.stackParent``.

    Returns
    -------
    StackParent | None
        The parsed parent, or ``None`` when the value does not match the
        grammar.

    """
    version, _, remainder = value.partition(":")
    if version != RECORD_VERSION:
        return None
    kind, _, body = remainder.partition(":")
    if kind == "branch" and _component_rejection(body) is None:
        return StackParent(branch=body, pull_request=None)
    if kind != "pr":
        return None
    identity = parse_pull_request_identity(body)
    if identity is None:
        return None
    return StackParent(branch=None, pull_request=identity)


def render_parent(parent: StackParent) -> str:
    """Render a ``StackParent`` in the versioned form ``parse_parent`` accepts.

    Parameters
    ----------
    parent : StackParent
        Parent to render.

    Returns
    -------
    str
        The versioned value to store.

    Raises
    ------
    ValueError
        If the parent names neither a branch nor a pull request.

    """
    if parent.branch is not None:
        return f"{RECORD_VERSION}:branch:{parent.branch}"
    if parent.pull_request is not None:
        identity = parent.pull_request
        return f"{RECORD_VERSION}:pr:{identity.repository}#{identity.number}"
    msg = "a stack parent names a branch or a pull request"
    raise ValueError(msg)


def should_record(
    base_ref: str, base_commit: str, trunk_ref: str, trunk_commit: str
) -> bool:
    """Return whether a branch created from this base is stacked.

    False when the base resolves to the trunk, by ref name or by commit. This
    is the whole of INV-11's decision, kept pure so both directions are a table
    test rather than a repository fixture. Both commits must be resolved object
    IDs; the caller resolves each ref once and passes the frozen result.

    Parameters
    ----------
    base_ref : str
        Ref the new branch's base was selected by.
    base_commit : str
        Commit that ref resolved to.
    trunk_ref : str
        Ref naming the trunk, typically the advertised default branch.
    trunk_commit : str
        Commit the trunk resolved to.

    Returns
    -------
    bool
        Whether a record should be written for the new branch.

    """
    return base_ref != trunk_ref and base_commit != trunk_commit


def _is_object_id(value: str) -> bool:
    """Return whether ``value`` is a full hexadecimal object ID."""
    return _OBJECT_ID_PATTERN.fullmatch(value) is not None


def _record_values(config: typ.Mapping[str, str]) -> dict[RecordKey, str]:
    """Return the record's keys from a branch configuration section.

    The lookup is case-insensitive because Git lower-cases variable names on
    read. Keys the record does not name are ignored, so an unrelated setting in
    the same section is not mistaken for a record.

    Parameters
    ----------
    config : typ.Mapping[str, str]
        Keys and values read from the branch's configuration section.

    Returns
    -------
    dict[RecordKey, str]
        The record's keys that are present, with their values.

    """
    folded = {key.lower(): value for key, value in config.items()}
    return {key: folded[key.value] for key in RecordKey if key.value in folded}


def _parse_record(
    branch: str, values: typ.Mapping[RecordKey, str]
) -> StackRecord | RecordMalformed:
    """Build a record from the configuration values, or report why it cannot."""
    missing = [key.value for key in RecordKey if not values.get(key)]
    if missing:
        return RecordMalformed(
            f"the record for {branch!r} is missing {', '.join(missing)}"
        )
    parent = parse_parent(values[RecordKey.PARENT])
    if parent is None:
        return RecordMalformed(
            f"the record for {branch!r} has an unreadable parent value "
            f"{values[RecordKey.PARENT]!r}"
        )
    base = values[RecordKey.BASE]
    if not _is_object_id(base):
        return RecordMalformed(
            f"the record for {branch!r} names a boundary that is not a full "
            f"object ID: {base!r}"
        )
    recorded_from = values[RecordKey.RECORDED_FROM]
    if not _is_object_id(recorded_from):
        return RecordMalformed(
            f"the record for {branch!r} names a child tip that is not a full "
            f"object ID: {recorded_from!r}"
        )
    return StackRecord(
        branch=branch,
        parent=parent,
        base=base,
        recorded_from=recorded_from,
        evidence=values[RecordKey.EVIDENCE],
    )


def record_values(record: StackRecord) -> dict[RecordKey, str]:
    """Render ``record`` as the configuration values that reproduce it.

    The values are parsed back before they are returned, so a record this
    module would not read back unchanged is refused here rather than written
    and reported malformed by every later read. That check is what makes the
    stored form trustworthy: the store cannot write a value the reader
    disagrees with, including one that names an abbreviated object ID or an
    empty evidence kind.

    Parameters
    ----------
    record : StackRecord
        The record to render.

    Returns
    -------
    dict[RecordKey, str]
        One value per record key, keyed by the canonical spelling.

    Raises
    ------
    ValueError
        If the record is not one this module's parser reads back unchanged.

    """
    values = {
        RecordKey.PARENT: render_parent(record.parent),
        RecordKey.BASE: record.base,
        RecordKey.RECORDED_FROM: record.recorded_from,
        RecordKey.EVIDENCE: record.evidence,
    }
    if _parse_record(record.branch, values) != record:
        msg = (
            f"the record for {record.branch!r} does not survive a round trip "
            "through its stored form"
        )
        raise ValueError(msg)
    return values


def _has_artefacts(values: typ.Mapping[RecordKey, str], anchor: str | None) -> bool:
    """Return whether either artefact of a record is present."""
    return bool(values) or anchor is not None


def reconcile(
    branch: str,
    config: typ.Mapping[str, str],
    anchor: str | None,
    *,
    branch_exists: bool,
) -> RecordResult:
    """Combine the configuration and the anchor ref into one record result.

    Configuration is authoritative for values; the anchor is authoritative
    only for reachability. Configuration present with a disagreeing anchor is
    ``RecordMalformed``, never a choice between the two. Configuration present
    with a missing anchor is a valid record whose boundary is at risk, which
    the caller reports. An anchor with no configuration is ``RecordMalformed``,
    because ``git branch -m`` carries configuration and leaves the anchor
    behind.

    Parameters
    ----------
    branch : str
        Branch the artefacts were read for.
    config : typ.Mapping[str, str]
        Keys and values read from that branch's configuration section.
    anchor : str | None
        Commit the anchor ref names, or ``None`` when it does not exist.
    branch_exists : bool
        Whether the branch itself still exists.

    Returns
    -------
    RecordResult
        One of ``StackRecord``, ``RecordAbsent``, ``RecordMalformed``, or
        ``RecordOrphaned``.

    """
    values = _record_values(config)
    if not branch_exists and _has_artefacts(values, anchor):
        return RecordOrphaned(branch)
    if not values:
        if anchor is None:
            return RecordAbsent()
        return RecordMalformed(
            f"an anchor for {branch!r} exists without configuration, which is "
            "what a branch rename leaves behind"
        )
    record = _parse_record(branch, values)
    if isinstance(record, RecordMalformed):
        return record
    if anchor is not None and anchor != record.base:
        return RecordMalformed(
            f"the anchor for {branch!r} names {anchor} but the configuration "
            f"names {record.base}"
        )
    return record
