"""Pure comparison policy for the incoming and outgoing workflows.

This module contains no GitPython, filesystem, or process mutation. It decides
which remote owns a comparison ref and which refs bound a comparison, so the
workflow module can perform Git work, render results, and map exit codes.
"""

from __future__ import annotations

import typing as typ

_REFS_REMOTES_PREFIX = "refs/remotes/"
_HEAD_REF = "HEAD"


def remote_name_for_ref(remote_names: typ.Iterable[str], ref: str) -> str | None:
    """Return the configured remote that owns ``ref``, if any.

    A remote owns the ref when the normalized ref names it exactly or starts
    with it followed by a slash. Nested remote names are common enough that
    the longest match wins: ``team/core/main`` belongs to ``team/core`` even
    when a ``team`` remote is also configured, whatever the order of
    ``remote_names``.

    Parameters
    ----------
    remote_names : typing.Iterable[str]
        Configured remote names, in any order.
    ref : str
        Comparison ref, either the short ``<remote>/<branch>`` form or a
        canonical ``refs/remotes/<remote>/<branch>`` ref.

    Returns
    -------
    str | None
        The longest configured remote name owning ``ref``, or ``None`` when
        no configured remote owns it.

    Examples
    --------
    >>> remote_name_for_ref(["origin"], "origin/main")
    'origin'
    >>> remote_name_for_ref(["team", "team/core"], "team/core/main")
    'team/core'
    >>> remote_name_for_ref(["origin"], "main") is None
    True

    """
    normalized_ref = ref.removeprefix(_REFS_REMOTES_PREFIX)
    matches = [
        remote_name
        for remote_name in remote_names
        if normalized_ref == remote_name or normalized_ref.startswith(f"{remote_name}/")
    ]
    return max(matches, key=len, default=None)


def comparison_range(
    *,
    direction: typ.Literal["incoming", "outgoing"],
    ref: str,
) -> tuple[str, str]:
    """Return the include and exclude refs for ``direction``.

    An incoming comparison takes the comparison ref as the include side and
    ``HEAD`` as the exclude side, so the printed commits are the ones a pull
    would bring in. An outgoing comparison is the mirror image: it takes
    ``HEAD`` as the include side and the comparison ref as the exclude side,
    so the printed commits are the ones a push would send.

    Parameters
    ----------
    direction : typing.Literal["incoming", "outgoing"]
        Comparison direction.
    ref : str
        Comparison ref, the local or remote-tracking ref the comparison is
        made against.

    Returns
    -------
    tuple[str, str]
        The include ref and exclude ref, in that order, for ``direction``.

    Examples
    --------
    >>> comparison_range(direction="incoming", ref="origin/main")
    ('origin/main', 'HEAD')
    >>> comparison_range(direction="outgoing", ref="origin/main")
    ('HEAD', 'origin/main')

    """
    if direction == "incoming":
        return ref, _HEAD_REF
    return _HEAD_REF, ref
