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
        assert isinstance(result, str), "checksum should be returned as a str"
        assert result.islower(), "checksum should be lowercase"
        assert "=" not in result, "padding should be stripped by default"

    def test_golden_value_checksum(self) -> None:
        """Golden-value test to detect unintended algorithm changes."""
        # If this assertion breaks, the checksum / encoding logic has changed.
        result = slugs.adler32_base32_lc("hello")
        assert result == "aywaefi", "checksum encoding of 'hello' should be stable"

    def test_strip_padding_true(self) -> None:
        """Test that padding is stripped when strip_padding=True."""
        result = slugs.adler32_base32_lc("test", strip_padding=True)
        assert "=" not in result, "strip_padding=True should remove padding"

    def test_strip_padding_false(self) -> None:
        """Test that padding is kept when strip_padding=False."""
        result = slugs.adler32_base32_lc("test", strip_padding=False)
        # Base32 encoding of 4 bytes should have padding
        assert "=" in result, "strip_padding=False should keep base32 padding"
        assert len(result) == 8, "4-byte base32 with padding should be 8 chars"

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
            assert result1 == result2, "equal inputs should yield equal checksums"
        else:
            assert result1 != result2, (
                "different inputs should yield different checksums"
            )

    def test_empty_string(self) -> None:
        """Test checksum of empty string."""
        result = slugs.adler32_base32_lc("")
        assert isinstance(result, str), "empty input should still return a str"
        assert len(result) > 0, "empty input should still produce a checksum"

    def test_unicode_input(self) -> None:
        """Test checksum with unicode characters."""
        result = slugs.adler32_base32_lc("hello 世界")
        assert isinstance(result, str), "unicode input should return a str"
        assert result.islower(), "unicode input checksum should be lowercase"

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
        assert isinstance(result, str), "slug should be returned as a str"
        assert "-" in result, "slug should join parts with a dash"
        parts = result.rsplit("-", 1)
        assert len(parts) == 2, "slug should split into a body and a checksum"
        assert parts[0] == "hello-world", "body should be the slugified input"
        assert len(parts[1]) > 0, "slug should include a non-empty checksum"

    def test_url_slug(self) -> None:
        """Test slug generation from URL."""
        url = "https://github.com/user/repo.git"
        result = slugs.slug_dash_adler32(url)
        assert isinstance(result, str), "URL slug should be returned as a str"
        assert result.startswith("https-github-com-user-repo-git-"), (
            "URL should be slugified before the checksum suffix"
        )

    def test_same_input_same_output(self) -> None:
        """Test that same input produces same slug."""
        text = "feature/my-branch"
        result1 = slugs.slug_dash_adler32(text)
        result2 = slugs.slug_dash_adler32(text)
        assert result1 == result2, "same input should produce the same slug"

    def test_different_inputs_different_outputs(self) -> None:
        """Test that different inputs produce different slugs."""
        result1 = slugs.slug_dash_adler32("branch-one")
        result2 = slugs.slug_dash_adler32("branch-two")
        assert result1 != result2, "different inputs should produce different slugs"

    def test_checksum_based_on_original(self) -> None:
        """Test that checksum is based on original text, not slugified."""
        # "Hello World" and "hello world" slugify to same thing but have
        # different checksums
        result1 = slugs.slug_dash_adler32("Hello World")
        result2 = slugs.slug_dash_adler32("hello world")
        checksum1 = result1.rsplit("-", 1)[1]
        checksum2 = result2.rsplit("-", 1)[1]
        assert checksum1 != checksum2, (
            "checksum should derive from original text, not the slug"
        )

    def test_special_characters(self) -> None:
        """Test slug with special characters."""
        result = slugs.slug_dash_adler32("feature/my@branch#123")
        assert isinstance(result, str), "slug should be returned as a str"
        # Should be slugified to remove special chars
        assert "@" not in result, "slug should strip '@' characters"
        assert "#" not in result, "slug should strip '#' characters"
        assert "/" not in result, "slug should strip '/' characters"

    def test_empty_string(self) -> None:
        """Test slug generation from empty string."""
        result = slugs.slug_dash_adler32("")
        assert isinstance(result, str), "empty input should still return a str"
        # Empty string slugifies to empty, but checksum is appended
        assert result.startswith("-"), "empty slug body should leave a leading dash"

    def test_type_error_non_string(self) -> None:
        """Test that TypeError is raised for non-string input."""
        non_string = typ.cast("str", 123)
        with pytest.raises(TypeError, match="text must be str"):
            slugs.slug_dash_adler32(non_string)
