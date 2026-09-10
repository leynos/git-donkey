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
    """Return the configured remote that owns ``ref``, if any."""
    normalized_ref = ref.removeprefix(_REFS_REMOTES_PREFIX)
    for remote_name in remote_names:
        if normalized_ref == remote_name or normalized_ref.startswith(
            f"{remote_name}/"
        ):
            return remote_name
    return None


def comparison_range(
    *,
    direction: typ.Literal["incoming", "outgoing"],
    ref: str,
) -> tuple[str, str]:
    """Return the include and exclude refs for ``direction``."""
    if direction == "incoming":
        return ref, _HEAD_REF
    return _HEAD_REF, ref
