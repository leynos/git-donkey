"""Shared package constants for git-donkey.

Provides canonical names used across the package, keeping CLI labels and
metadata consistent.

Usage
-----
Import the package name when building user-facing messages::

    from git_donkey._constants import PACKAGE_NAME
    print(PACKAGE_NAME)
"""

from __future__ import annotations

PACKAGE_NAME = "git_donkey"
GIT_PLONK_PREFIX = "git-plonk"
GIT_WHERESAT_PREFIX = "git-wheresat"

# The two exit statuses Git reports for a question it answers with yes and no.
# Both readings are named together, because a reader that names one of them and
# treats every other status as the other reading cannot tell a negative answer
# from a Git that failed.
GIT_ANSWERED_YES = 0
GIT_ANSWERED_NO = 1

# What asks ``git for-each-ref`` for a ref's full name and nothing else, so the
# answer can be read as a list of refs without parsing anything around them.
REF_NAME_FORMAT = "--format=%(refname)"

# Refs a transported boundary would be fetched into, one namespace per run. A
# module that needs the per-run prefix derives it from this name rather than
# restating the namespace, because two spellings of one namespace are two
# places for it to change.
WHERESAT_OPERATION_NAMESPACE = "refs/wheresat/op"
