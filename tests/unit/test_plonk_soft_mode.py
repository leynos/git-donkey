"""Unit tests for ``git-plonk``'s soft pass.

Soft mode is the one sweep that resolves no trunk at all: it loads only the
worktree context and works on the generated paths ``git donkey`` leaves behind.
A real run removes those directories and leaves everything else, Git state
included, exactly as it found it; a dry run reports the same paths without
removing them. These tests pin that narrow contract.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from git_donkey import layout, plonk


def test_soft_mode_loads_only_worktree_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Soft mode should avoid trunk ref resolution and global cwd changes."""
    worktrees_root = tmp_path / "repo.worktrees"
    stanzas = [
        {"worktree": tmp_path / "repo", "branch": "refs/heads/main"},
        {"worktree": worktrees_root / "issue-123-fix"},
    ]
    cwd_before = Path.cwd()

    monkeypatch.setattr(plonk.helpers, "_find_repo", lambda _prefix: SimpleNamespace())
    monkeypatch.setattr(
        plonk.helpers, "_parse_worktree_porcelain", lambda _repo: stanzas
    )
    monkeypatch.setattr(
        plonk.layout,
        "resolve_layout",
        lambda _repo, _stanzas: layout.RepositoryLayout(
            anchor_dir=tmp_path / "repo",
            kind=layout.LayoutKind.MAIN_WORKTREE,
            worktrees_root=worktrees_root,
        ),
    )
    monkeypatch.setattr(
        plonk,
        "_load_plonk_context",
        lambda: pytest.fail("soft mode should not load trunk cleanup context"),
    )
    monkeypatch.setattr(
        plonk,
        "_fetch_canonical_trunk_ref",
        lambda _repo: pytest.fail("soft mode should not resolve trunk history"),
    )
    monkeypatch.setattr(
        plonk.os,
        "chdir",
        lambda _path: pytest.fail("soft mode should not mutate cwd"),
    )

    exit_code = plonk.run_git_plonk(soft=True)

    assert exit_code == 0, "expected soft git plonk to succeed"
    assert Path.cwd() == cwd_before, "soft mode must not change the process cwd"
    assert "git-plonk: mode=soft" in capsys.readouterr().out, (
        "expected soft-mode summary output"
    )


def test_soft_mode_removes_generated_paths_and_leaves_the_rest(
    tmp_path: Path,
) -> None:
    """A real soft run should remove the generated paths and nothing else."""
    worktrees_root = tmp_path / "repo.worktrees"
    worktree_path = worktrees_root / "issue-123-fix"
    target_path = worktree_path / "target"
    target_path.mkdir(parents=True)
    work_file = worktree_path / "README.md"
    work_file.write_text("work in progress")
    anchor = tmp_path / "repo"

    stanzas: list[dict[str, object]] = [
        {"worktree": anchor, "branch": "refs/heads/main"},
        {"worktree": worktree_path},
    ]

    result = plonk._run_soft(
        plonk._SoftPlonkContext(
            stanzas=stanzas,
            worktrees_root=worktrees_root,
            anchor_dir=anchor,
        )
    )

    assert result.mode is plonk._PlonkMode.SOFT, "expected soft mode"
    assert not result.is_dry_run, "a real soft run is not a plan"
    assert result.cleaned_paths == (target_path,), (
        "the generated path is the only removal the run reports"
    )
    assert not target_path.exists(), "the generated directory is really removed"
    assert work_file.read_text() == "work in progress", (
        "soft mode removes generated paths, never the worktree's own files"
    )


def test_dry_run_soft_mode_reports_targets_without_removing_them(
    tmp_path: Path,
) -> None:
    """Dry-run soft mode should inspect generated paths without deleting them."""
    worktrees_root = tmp_path / "repo.worktrees"
    worktree_path = worktrees_root / "issue-123-fix"
    target_path = worktree_path / "target"
    target_path.mkdir(parents=True)
    anchor = tmp_path / "repo"

    stanzas: list[dict[str, object]] = [
        {"worktree": anchor, "branch": "refs/heads/main"},
        {"worktree": worktree_path},
    ]

    result = plonk._run_soft(
        plonk._SoftPlonkContext(
            stanzas=stanzas,
            worktrees_root=worktrees_root,
            anchor_dir=anchor,
        ),
        dry_run=True,
    )

    assert result.mode is plonk._PlonkMode.SOFT, "expected soft dry-run mode"
    assert result.is_dry_run, "expected dry-run result marker"
    assert result.cleaned_paths == (target_path,), "expected planned target removal"
    assert target_path.is_dir(), "expected dry run to leave generated path intact"
