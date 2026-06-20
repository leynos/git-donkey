"""Unit tests for ``git_donkey.slugs``.

The slug helpers provide stable path components for worktree and template
directories. These tests pin checksum behaviour and runtime type guards used by
the higher-level workflow modules.
"""

from __future__ import annotations

import typing as typ

import pytest

from git_donkey import slugs


class TestAdler32Base32Lc:
    """Test adler32_base32_lc function."""

    def test_basic_checksum(self) -> None:
        """Test basic checksum generation."""
        result = slugs.adler32_base32_lc("hello")
        assert isinstance(result, str)
        assert result.islower()
        assert "=" not in result  # padding stripped by default

    def test_golden_value_checksum(self) -> None:
        """Golden-value test to detect unintended algorithm changes."""
        # If this assertion breaks, the checksum / encoding logic has changed.
        result = slugs.adler32_base32_lc("hello")
        assert result == "aywaefi"

    def test_strip_padding_true(self) -> None:
        """Test that padding is stripped when strip_padding=True."""
        result = slugs.adler32_base32_lc("test", strip_padding=True)
        assert "=" not in result

    def test_strip_padding_false(self) -> None:
        """Test that padding is kept when strip_padding=False."""
        result = slugs.adler32_base32_lc("test", strip_padding=False)
        # Base32 encoding of 4 bytes should have padding
        assert "=" in result
        assert len(result) == 8  # 4 bytes in base32 with padding

    @pytest.mark.parametrize(
        ("input1", "input2", "expect_equal"),
        [
            # Same input should produce same output
            (
                "https://github.com/user/repo.git",
                "https://github.com/user/repo.git",
                True,
            ),
            # Different inputs should produce different outputs
            (
                "https://github.com/user/repo1.git",
                "https://github.com/user/repo2.git",
                False,
            ),
        ],
    )
    def test_adler32_base32_lc_consistency(
        self,
        input1: str,
        input2: str,
        *,
        expect_equal: bool,
    ) -> None:
        """Test consistency and collision properties of adler32_base32_lc."""
        result1 = slugs.adler32_base32_lc(input1)
        result2 = slugs.adler32_base32_lc(input2)
        if expect_equal:
            assert result1 == result2
        else:
            assert result1 != result2

    def test_empty_string(self) -> None:
        """Test checksum of empty string."""
        result = slugs.adler32_base32_lc("")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_unicode_input(self) -> None:
        """Test checksum with unicode characters."""
        result = slugs.adler32_base32_lc("hello 世界")
        assert isinstance(result, str)
        assert result.islower()

    def test_type_error_non_string(self) -> None:
        """Test that TypeError is raised for non-string input."""
        non_string = typ.cast("str", 123)
        with pytest.raises(TypeError, match="text must be str"):
            slugs.adler32_base32_lc(non_string)


class TestSlugDashAdler32:
    """Test slug_dash_adler32 function."""

    def test_basic_slug(self) -> None:
        """Test basic slug generation."""
        result = slugs.slug_dash_adler32("Hello World")
        assert isinstance(result, str)
        assert "-" in result
        parts = result.rsplit("-", 1)
        assert len(parts) == 2
        assert parts[0] == "hello-world"  # slugified
        assert len(parts[1]) > 0  # checksum part

    def test_url_slug(self) -> None:
        """Test slug generation from URL."""
        url = "https://github.com/user/repo.git"
        result = slugs.slug_dash_adler32(url)
        assert isinstance(result, str)
        assert result.startswith("https-github-com-user-repo-git-")

    def test_same_input_same_output(self) -> None:
        """Test that same input produces same slug."""
        text = "feature/my-branch"
        result1 = slugs.slug_dash_adler32(text)
        result2 = slugs.slug_dash_adler32(text)
        assert result1 == result2

    def test_different_inputs_different_outputs(self) -> None:
        """Test that different inputs produce different slugs."""
        result1 = slugs.slug_dash_adler32("branch-one")
        result2 = slugs.slug_dash_adler32("branch-two")
        assert result1 != result2

    def test_checksum_based_on_original(self) -> None:
        """Test that checksum is based on original text, not slugified."""
        # "Hello World" and "hello world" slugify to same thing but have
        # different checksums
        result1 = slugs.slug_dash_adler32("Hello World")
        result2 = slugs.slug_dash_adler32("hello world")
        checksum1 = result1.rsplit("-", 1)[1]
        checksum2 = result2.rsplit("-", 1)[1]
        assert checksum1 != checksum2

    def test_special_characters(self) -> None:
        """Test slug with special characters."""
        result = slugs.slug_dash_adler32("feature/my@branch#123")
        assert isinstance(result, str)
        # Should be slugified to remove special chars
        assert "@" not in result
        assert "#" not in result
        assert "/" not in result

    def test_empty_string(self) -> None:
        """Test slug generation from empty string."""
        result = slugs.slug_dash_adler32("")
        assert isinstance(result, str)
        # Empty string slugifies to empty, but checksum is appended
        assert result.startswith("-")

    def test_type_error_non_string(self) -> None:
        """Test that TypeError is raised for non-string input."""
        non_string = typ.cast("str", 123)
        with pytest.raises(TypeError, match="text must be str"):
            slugs.slug_dash_adler32(non_string)
