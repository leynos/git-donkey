"""Decide whether a remote can track the branches a fetch updates.

``git clone --bare`` configures no fetch refspec for its remote. A fetch then
updates no ``refs/remotes/<remote>/*`` ref, so a branch that exists on the
remote looks absent, and ``git donkey`` would create a fresh branch from the
default where it should have tracked the remote one.

:func:`refusal_message` is the pure rule. The Git read that supplies its input
stays with the caller, so the rule can be tested without a repository and the
caller alone decides how to report an unreadable configuration.
"""

from __future__ import annotations

import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc


class FetchRefspecError(Exception):
    """A remote's fetch configuration could not be read.

    This is distinct from a readable but empty value: an unreadable
    configuration must not be reported as a missing fetch refspec.
    """


def refusal_message(remote: str, refspecs: cabc.Sequence[str]) -> str | None:
    """Return why ``remote`` cannot track branches, or ``None`` when it can.

    Parameters
    ----------
    remote : str
        Name of the remote whose configuration was read.
    refspecs : cabc.Sequence[str]
        The configured ``remote.<remote>.fetch`` values. An empty sequence
        covers both an unset key and a key configured with no value.

    Returns
    -------
    str | None
        A message naming the remote and the command that restores branch
        tracking, or ``None`` when at least one refspec is configured.

    Examples
    --------
    >>> refusal_message("origin", ["+refs/heads/*:refs/remotes/origin/*"]) is None
    True
    >>> "no fetch refspec" in (refusal_message("origin", []) or "")
    True

    """
    if refspecs:
        return None
    return (
        f"remote '{remote}' has no fetch refspec, so its branches cannot be "
        "tracked (`git clone --bare` configures none); run: "
        f"git config remote.{remote}.fetch '+refs/heads/*:refs/remotes/{remote}/*'"
    )
