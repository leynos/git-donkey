"""Unit tests for git_donkey.templates module."""

from __future__ import annotations

import typing as typ
from pathlib import Path
from unittest import mock

import pytest
from git import Repo

from git_donkey import helpers, slugs, templates


@pytest.fixture
def mock_template_base_dir(tmp_path: Path) -> typ.Generator[Path]:
    """Fixture that patches _get_template_base_dir to return tmp_path."""
    with mock.patch(
        "git_donkey.templates._get_template_base_dir", return_value=tmp_path
    ):
        yield tmp_path


class TestGetTemplateBaseDir:
    """Test _get_template_base_dir function."""

    def test_returns_path(self) -> None:
        """Test that the function returns a Path object."""
        result = templates._get_template_base_dir()
        assert isinstance(result, Path), "expected result to be a Path object"
        assert result.name == "template", (
            "expected last path component to be 'template'"
        )
        assert result.parent.name == "git-donkey", (
            "expected parent directory to be 'git-donkey'"
        )


class TestGetRepoUrl:
    """Test _get_repo_url function."""

    def test_no_remotes(self) -> None:
        """Test that None is returned when repo has no remotes."""
        mock_repo = mock.Mock(spec=Repo)
        mock_repo.remotes = []
        result = templates._get_repo_url(mock_repo)
        assert result is None

    def test_with_remote(self) -> None:
        """Test that URL is returned when repo has remotes."""
        mock_remote = mock.Mock()
        mock_remote.url = "https://github.com/user/repo.git"
        mock_repo = mock.Mock(spec=Repo)
        mock_repo.remotes = [mock_remote]
        result = templates._get_repo_url(mock_repo)
        assert result == "https://github.com/user/repo.git"

    def test_multiple_remotes_uses_first(self) -> None:
        """Test that first remote's URL is used when multiple remotes exist."""
        mock_remote1 = mock.Mock()
        mock_remote1.name = "upstream"
        mock_remote1.url = "https://github.com/user/repo1.git"
        mock_remote2 = mock.Mock()
        mock_remote2.name = "fork"
        mock_remote2.url = "https://github.com/user/repo2.git"
        mock_repo = mock.Mock(spec=Repo)
        mock_repo.remotes = [mock_remote1, mock_remote2]
        result = templates._get_repo_url(mock_repo)
        assert result == "https://github.com/user/repo1.git"

    def test_multiple_remotes_prefers_origin(self) -> None:
        """Test that origin remote is preferred over other remotes."""
        mock_remote1 = mock.Mock()
        mock_remote1.name = "upstream"
        mock_remote1.url = "https://github.com/user/repo1.git"
        mock_remote2 = mock.Mock()
        mock_remote2.name = "origin"
        mock_remote2.url = "https://github.com/user/repo2.git"
        mock_repo = mock.Mock(spec=Repo)
        mock_repo.remotes = [mock_remote1, mock_remote2]
        result = templates._get_repo_url(mock_repo)
        assert result == "https://github.com/user/repo2.git"


class TestGetTemplateDirPath:
    """Test get_template_dir_path function."""

    def test_no_remotes_returns_none(self) -> None:
        """Test that None is returned when repo has no remotes."""
        mock_repo = mock.Mock(spec=Repo)
        mock_repo.remotes = []
        result = templates.get_template_dir_path(mock_repo)
        assert result is None

    def test_with_remote_returns_path(self, mock_template_base_dir: Path) -> None:
        """Test that path is returned when repo has remote."""
        mock_remote = mock.Mock()
        mock_remote.url = "https://github.com/user/repo.git"
        mock_repo = mock.Mock(spec=Repo)
        mock_repo.remotes = [mock_remote]

        repo_slug = slugs.slug_dash_adler32(mock_remote.url)
        expected_path = mock_template_base_dir / repo_slug

        result = templates.get_template_dir_path(mock_repo)
        assert result == expected_path


class TestGetTemplateDir:
    """Test get_template_dir function."""

    def test_no_remotes_returns_none(self) -> None:
        """Test that None is returned when repo has no remotes."""
        mock_repo = mock.Mock(spec=Repo)
        mock_repo.remotes = []
        result = templates.get_template_dir(mock_repo)
        assert result is None

    def test_template_dir_not_exists_returns_none(
        self, mock_template_base_dir: Path
    ) -> None:
        """Test that None is returned when template directory doesn't exist."""
        mock_remote = mock.Mock()
        mock_remote.url = "https://github.com/user/repo.git"
        mock_repo = mock.Mock(spec=Repo)
        mock_repo.remotes = [mock_remote]

        result = templates.get_template_dir(mock_repo)
        assert result is None

    def test_template_dir_exists_returns_path(
        self, mock_template_base_dir: Path
    ) -> None:
        """Test that path is returned when template directory exists."""
        mock_remote = mock.Mock()
        mock_remote.url = "https://github.com/user/repo.git"
        mock_repo = mock.Mock(spec=Repo)
        mock_repo.remotes = [mock_remote]

        # Create the expected directory structure
        repo_slug = slugs.slug_dash_adler32(mock_remote.url)
        template_path = mock_template_base_dir / repo_slug
        template_path.mkdir(parents=True)

        result = templates.get_template_dir(mock_repo)
        assert result == template_path

    def test_template_path_is_file_returns_none(
        self, mock_template_base_dir: Path
    ) -> None:
        """Test that None is returned when template path is a file, not a directory."""
        mock_remote = mock.Mock()
        mock_remote.url = "https://github.com/user/repo.git"
        mock_repo = mock.Mock(spec=Repo)
        mock_repo.remotes = [mock_remote]

        repo_slug = slugs.slug_dash_adler32(mock_remote.url)
        template_path = mock_template_base_dir / repo_slug

        # Make template_path a file instead of directory
        template_path.parent.mkdir(parents=True, exist_ok=True)
        template_path.write_text("not a directory")

        result = templates.get_template_dir(mock_repo)
        assert result is None


class TestApplyTemplate:
    """Test apply_template function."""

    def test_template_dir_not_exists_raises(self, tmp_path: Path) -> None:
        """Test that ValueError is raised when template directory doesn't exist."""
        template_dir = tmp_path / "nonexistent"
        target_dir = tmp_path / "target"
        target_dir.mkdir()

        with pytest.raises(ValueError, match="does not exist"):
            templates.apply_template(template_dir, target_dir, prefix="TEST")

    def test_template_path_is_file_raises(self, tmp_path: Path) -> None:
        """Test that ValueError is raised when template path is a file."""
        template_file = tmp_path / "template"
        template_file.write_text("not a directory")
        target_dir = tmp_path / "target"
        target_dir.mkdir()

        with pytest.raises(ValueError, match="not a directory"):
            templates.apply_template(template_file, target_dir, prefix="TEST")

    def test_copy_single_file(self, tmp_path: Path) -> None:
        """Test copying a single file from template to target."""
        template_dir = tmp_path / "template"
        template_dir.mkdir()
        (template_dir / "file.txt").write_text("content")

        target_dir = tmp_path / "target"
        target_dir.mkdir()

        conflicts = templates.apply_template(template_dir, target_dir, prefix="TEST")

        assert (target_dir / "file.txt").exists(), "expected file.txt to be copied"
        assert (target_dir / "file.txt").read_text() == "content", (
            "expected file content to match template"
        )
        assert conflicts == [], "expected no conflicts for new file"

    def test_copy_nested_files(self, tmp_path: Path) -> None:
        """Test copying nested directory structure."""
        template_dir = tmp_path / "template"
        template_dir.mkdir()
        (template_dir / "subdir").mkdir()
        (template_dir / "subdir" / "nested.txt").write_text("nested content")
        (template_dir / "root.txt").write_text("root content")

        target_dir = tmp_path / "target"
        target_dir.mkdir()

        conflicts = templates.apply_template(template_dir, target_dir, prefix="TEST")

        assert (target_dir / "root.txt").exists(), "expected root.txt to be copied"
        assert (target_dir / "subdir" / "nested.txt").exists(), (
            "expected nested.txt to be copied"
        )
        assert (target_dir / "subdir" / "nested.txt").read_text() == "nested content", (
            "expected nested file content to match template"
        )
        assert conflicts == [], "expected no conflicts for nested files"

    def test_overwrites_existing_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that existing files are overwritten and conflicts are reported."""
        template_dir = tmp_path / "template"
        template_dir.mkdir()
        (template_dir / "file.txt").write_text("new content")

        target_dir = tmp_path / "target"
        target_dir.mkdir()
        existing_file = target_dir / "file.txt"
        existing_file.write_text("old content")

        # Capture user-facing warnings emitted via helpers._eprint
        messages: list[str] = []

        def fake_eprint(message: str) -> None:
            messages.append(message)

        monkeypatch.setattr(helpers, "_eprint", fake_eprint)

        conflicts = templates.apply_template(template_dir, target_dir, prefix="TEST")

        assert (target_dir / "file.txt").exists(), (
            "expected file.txt to exist after overwrite"
        )
        assert (target_dir / "file.txt").read_text() == "new content", (
            "expected file content to be updated"
        )

        # The overwritten file should be reported as a conflict
        assert Path("file.txt") in conflicts, (
            "expected overwritten file to be reported as conflict"
        )

        # A warning with the prefix and overwriting wording should be emitted
        assert any("TEST" in msg for msg in messages), (
            "expected warning to include prefix"
        )
        assert any("overwriting" in msg.lower() for msg in messages), (
            "expected warning to mention overwriting"
        )

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        """Test that parent directories are created as needed."""
        template_dir = tmp_path / "template"
        template_dir.mkdir()
        (template_dir / "deep" / "nested" / "dir").mkdir(parents=True)
        (template_dir / "deep" / "nested" / "dir" / "file.txt").write_text("content")

        target_dir = tmp_path / "target"
        target_dir.mkdir()

        conflicts = templates.apply_template(template_dir, target_dir, prefix="TEST")

        assert (target_dir / "deep" / "nested" / "dir" / "file.txt").exists(), (
            "expected deeply nested file to be created"
        )
        assert conflicts == [], "expected no conflicts when creating directories"

    def test_preserves_file_metadata(self, tmp_path: Path) -> None:
        """Test that file metadata (timestamps) are preserved."""
        template_dir = tmp_path / "template"
        template_dir.mkdir()
        template_file = template_dir / "file.txt"
        template_file.write_text("content")

        # Get original stat
        original_stat = template_file.stat()

        target_dir = tmp_path / "target"
        target_dir.mkdir()

        templates.apply_template(template_dir, target_dir, prefix="TEST")

        target_file = target_dir / "file.txt"
        target_stat = target_file.stat()

        # shutil.copy2 preserves modification time (allow small tolerance)
        assert target_stat.st_mtime == pytest.approx(
            original_stat.st_mtime, rel=0, abs=1
        ), "expected modification time to be preserved"

    def test_empty_template_directory(self, tmp_path: Path) -> None:
        """Test that empty template directory results in no files copied."""
        template_dir = tmp_path / "template"
        template_dir.mkdir()

        target_dir = tmp_path / "target"
        target_dir.mkdir()

        conflicts = templates.apply_template(template_dir, target_dir, prefix="TEST")

        # Only the target directory should exist, no files copied
        assert list(target_dir.iterdir()) == [], (
            "expected no files to be copied from empty template"
        )
        assert conflicts == [], "expected no conflicts from empty template"

    def test_multiple_conflicts(self, tmp_path: Path) -> None:
        """Test that multiple conflicts are all reported."""
        template_dir = tmp_path / "template"
        template_dir.mkdir()
        (template_dir / "file1.txt").write_text("content1")
        (template_dir / "file2.txt").write_text("content2")

        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "file1.txt").write_text("old1")
        (target_dir / "file2.txt").write_text("old2")

        conflicts = templates.apply_template(template_dir, target_dir, prefix="TEST")

        assert len(conflicts) == 2, "expected two conflicts to be reported"
        assert Path("file1.txt") in conflicts, (
            "expected file1.txt to be reported as conflict"
        )
        assert Path("file2.txt") in conflicts, (
            "expected file2.txt to be reported as conflict"
        )

    def test_skips_directory_collision(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that template file is skipped when target is a directory."""
        template_dir = tmp_path / "template"
        template_dir.mkdir()
        # Template has a file named "config"
        (template_dir / "config").write_text("config content")

        target_dir = tmp_path / "target"
        target_dir.mkdir()
        # Target has a directory named "config"
        config_dir = target_dir / "config"
        config_dir.mkdir()
        (config_dir / "nested.txt").write_text("nested file")

        # Capture warnings
        messages: list[str] = []

        def fake_eprint(message: str) -> None:
            messages.append(message)

        monkeypatch.setattr(helpers, "_eprint", fake_eprint)

        conflicts = templates.apply_template(template_dir, target_dir, prefix="TEST")

        # The directory should still exist and remain unchanged
        assert config_dir.is_dir(), "expected config directory to remain unchanged"
        assert (config_dir / "nested.txt").exists(), (
            "expected nested file to remain unchanged"
        )
        assert (config_dir / "nested.txt").read_text() == "nested file", (
            "expected nested file content to remain unchanged"
        )

        # The file should not have been created
        assert not (target_dir / "config").is_file(), (
            "expected config directory not to be overwritten with file"
        )

        # Should be reported as a conflict
        assert Path("config") in conflicts, (
            "expected directory collision to be reported as conflict"
        )

        # Warning should be emitted
        assert any("TEST" in msg for msg in messages), (
            "expected warning to include prefix"
        )
        assert any("directory" in msg.lower() for msg in messages), (
            "expected warning to mention directory"
        )
        assert any("skipping" in msg.lower() for msg in messages), (
            "expected warning to mention skipping"
        )
