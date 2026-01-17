"""Unit tests for CLI helpers."""

from __future__ import annotations

import pytest

from git_donkey import donkey, helpers


def test_choose_base_branch_defaults_to_main() -> None:
    """Defaulting to main should not mark the branch as coming from the CWD."""
    assert donkey.choose_base_branch("feature/demo", None) == "main"


def test_choose_base_branch_uses_cwd_on_dot() -> None:
    """A dot origin should select the saved CWD branch."""
    assert donkey.choose_base_branch("feature/demo", ".") == "feature/demo"


def test_validate_slug_rejects_invalid_chars() -> None:
    """Validation should exit with code 1 for invalid slugs."""
    with pytest.raises(SystemExit) as excinfo:
        helpers.validate_slug("bad slug", label="repo name", prefix="git-fafo")

    assert excinfo.value.code == 1


def test_validate_slug_accepts_valid_chars() -> None:
    """Validation should return the input when characters are valid."""
    assert (
        helpers.validate_slug("good-slug_1.2", label="repo name", prefix="git-fafo")
        == "good-slug_1.2"
    )
