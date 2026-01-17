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
