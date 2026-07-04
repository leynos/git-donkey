"""Package git workflow helpers used by the git-donkey command suite.

``git_donkey.__init__`` exposes ``PACKAGE_NAME`` for consumers that need the
installed distribution name. Command entrypoints live in ``git_donkey.cli`` and
delegate to workflow modules such as ``donkey``, ``track``, ``fafo`` and
``plonk``.
"""

from ._constants import PACKAGE_NAME

__all__ = ["PACKAGE_NAME"]
