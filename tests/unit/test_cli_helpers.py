"""Unit tests for shared CLI helper utilities.

Covers base-branch selection and slug validation logic used by git-donkey
commands.
"""

from __future__ import annotations

import pytest

from git_donkey import donkey, helpers


def test_choose_base_branch_defaults_to_main() -> None:
    """Defaulting to main should select main explicitly."""
    assert donkey.choose_base_branch("feature/demo", None) == "main", (
        "expected base branch to default to main"
    )


def test_choose_base_branch_uses_cwd_on_dot() -> None:
    """A dot origin should select the saved CWD branch."""
    assert donkey.choose_base_branch("feature/demo", ".") == "feature/demo", (
        "expected dot origin to select the saved CWD branch"
    )


@pytest.mark.parametrize(
    ("value", "expected_exit_code"),
    [
        ("good-slug_1.2", None),
        ("bad slug", 1),
    ],
)
def test_validate_slug(value: str, expected_exit_code: int | None) -> None:
    """Validate slugs for allowed characters and error handling."""
    if expected_exit_code is None:
        assert (
            helpers.validate_slug(value, label="repo name", prefix="git-fafo") == value
        ), "expected valid slug to be returned unchanged"
    else:
        with pytest.raises(SystemExit) as excinfo:
            helpers.validate_slug(value, label="repo name", prefix="git-fafo")

        assert excinfo.value.code == expected_exit_code, (
            "expected invalid slug to exit with code 1"
        )
