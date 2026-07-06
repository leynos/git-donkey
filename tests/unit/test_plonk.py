"""Unit tests for the ``git_donkey.plonk`` cleanup helpers.

The module pins pure completion-marker policy, command-line validation,
canonical trunk-ref selection, and deterministic summary rendering for
``git-plonk``. These tests provide fast feedback before the behaviour-driven
integration scenarios exercise destructive worktree cleanup against temporary
Git repositories.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path
from types import SimpleNamespace

import pytest
from git import Repo
from hypothesis import given
from hypothesis import strategies as st

from git_donkey import cli, plonk, plonk_policy

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


_ROADMAP_WORDS = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    min_size=1,
    max_size=8,
)


def test_issue_branch_marker_matches_issue_reference() -> None:
    """Issue branches should map to GitHub issue merge markers."""
    assert plonk_policy.completion_marker_for_branch("issue-123-fix-bug") == "(#123)", (
        "expected issue branch to map to GitHub issue marker"
    )


@given(namespace=st.none() | _ROADMAP_WORDS, suffix=st.none() | _ROADMAP_WORDS)
def test_roadmap_branch_marker_invariant(
    namespace: str | None,
    suffix: str | None,
) -> None:
    """Roadmap markers should be parenthesized dotted references."""
    prefix = f"{namespace}-" if namespace else ""
    suffix_part = suffix or ""
    branch_name = f"{prefix}1-20-300{suffix_part}-4-implement-plonk"

    marker = plonk_policy.completion_marker_for_branch(branch_name)

    assert marker is not None, "expected roadmap branch to produce a marker"
    assert marker.startswith("("), "expected roadmap marker to start with parenthesis"
    assert marker.endswith(")"), "expected roadmap marker to end with parenthesis"
    assert "-" not in marker, "expected roadmap marker to use dotted separators"
    assert ".." not in marker, "expected roadmap marker to avoid empty components"
    assert plonk_policy.has_completion_marker([f"Merge completed {marker}"], marker), (
        "expected exact roadmap marker to match history"
    )
    dotted_marker = f"{marker.removesuffix(')')}.)"
    assert plonk_policy.has_completion_marker(
        [f"Merge completed {dotted_marker}"], marker
    ), "expected dotted roadmap marker variant to match history"


@given(number=st.integers(min_value=1, max_value=999_999))
def test_issue_branch_marker_invariant(number: int) -> None:
    """Issue markers should match exact issue numbers in commit history."""
    marker = plonk_policy.completion_marker_for_branch(f"issue-{number}-short-title")

    assert marker == f"(#{number})", "expected issue marker to include issue number"
    assert marker is not None, "expected issue branch to produce a marker"
    assert plonk_policy.has_completion_marker([f"Squashed work {marker}"], marker), (
        "expected exact issue marker to match history"
    )
    assert not plonk_policy.has_completion_marker(
        [f"Squashed work (#{number + 1})"], marker
    ), "expected different issue marker not to match history"


def test_roadmap_branch_marker_includes_optional_namespace_suffix_and_task() -> None:
    """Roadmap branches should preserve namespace, suffix, and task number."""
    branch_name = "road-1-2-3a-4-finished-task"

    marker = plonk_policy.completion_marker_for_branch(branch_name)

    assert marker == "(road.1.2.3a.4)", (
        "expected roadmap marker to include namespace, suffix, and task"
    )


def test_unrecognized_branch_has_no_completion_marker() -> None:
    """Unrecognized branches should never be selected for default cleanup."""
    assert plonk_policy.completion_marker_for_branch("feature/unstructured") is None, (
        "expected unrecognized branch to have no completion marker"
    )


def test_completed_candidates_use_history_markers() -> None:
    """Candidate filtering should keep only branches with matching markers."""
    candidates = [
        plonk._PlonkCandidate(
            branch_name="issue-123-fix",
            worktree_path=Path("/repo.worktrees/issue-123-fix"),
            marker="(#123)",
        ),
        plonk._PlonkCandidate(
            branch_name="road-1-2-3a-4-task",
            worktree_path=Path("/repo.worktrees/road-1-2-3a-4-task"),
            marker="(road.1.2.3a.4)",
        ),
        plonk._PlonkCandidate(
            branch_name="issue-456-open",
            worktree_path=Path("/repo.worktrees/issue-456-open"),
            marker="(#456)",
        ),
    ]

    completed = plonk._completed_candidates(
        candidates,
        ["Merge pull request (#123)", "Roadmap complete (road.1.2.3a.4.)"],
    )

    assert [candidate.branch_name for candidate in completed] == [
        "issue-123-fix",
        "road-1-2-3a-4-task",
    ], "expected only candidates with matching history markers"


def test_completed_candidates_streams_history_until_markers_match() -> None:
    """Candidate filtering should not consume history after all markers match."""
    candidates = [
        plonk._PlonkCandidate(
            branch_name="issue-123-fix",
            worktree_path=Path("/repo.worktrees/issue-123-fix"),
            marker="(#123)",
        ),
        plonk._PlonkCandidate(
            branch_name="issue-456-fix",
            worktree_path=Path("/repo.worktrees/issue-456-fix"),
            marker="(#456)",
        ),
    ]

    consumed_messages = 0

    def messages() -> typ.Iterator[str]:
        nonlocal consumed_messages
        consumed_messages += 1
        yield "Merge pull request (#123)"
        consumed_messages += 1
        yield "Merge pull request (#456)"
        consumed_messages += 1
        yield "Unneeded late history"

    completed = plonk._completed_candidates(candidates, messages())

    assert [candidate.branch_name for candidate in completed] == [
        "issue-123-fix",
        "issue-456-fix",
    ], "expected streaming scan to stop after all markers match"
    assert consumed_messages == 2, (
        "expected history scan to stop after all markers match"
    )


def test_plonk_cli_rejects_soft_and_hard_together() -> None:
    """The CLI boundary should reject mutually exclusive cleanup modes."""
    with pytest.raises(SystemExit) as exc_info:
        cli._plonk_app(["--soft", "--hard"])

    assert exc_info.value.code == 2, "expected conflicting flags to be usage error"


def test_plonk_cli_passes_dry_run_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI boundary should pass dry-run intent to the workflow runner."""
    captured_options: dict[str, bool] = {}

    def run_plonk_stub(
        *,
        soft: bool = False,
        hard: bool = False,
        dry_run: bool = False,
    ) -> int:
        captured_options.update(soft=soft, hard=hard, dry_run=dry_run)
        return 0

    monkeypatch.setattr(plonk, "run_git_plonk", run_plonk_stub)

    with pytest.raises(SystemExit) as exc_info:
        cli._plonk_app(["--hard", "--dry-run"])

    assert exc_info.value.code == 0, "expected dry-run CLI invocation to succeed"
    assert captured_options == {"soft": False, "hard": True, "dry_run": True}, (
        "expected CLI to pass dry-run flag"
    )


def test_canonical_trunk_ref_prefers_remote_default_over_local_main(
    tmp_path: Path,
) -> None:
    """Remote HEAD should define canonical trunk before local ``main``."""
    repo = Repo.init(tmp_path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "Test User")
        config.set_value("user", "email", "test@example.com")
    seed_path = tmp_path / "README.md"
    seed_path.write_text("seed")
    repo.index.add([seed_path.as_posix()])
    repo.index.commit("Seed commit")
    repo.git.branch("-M", "main")
    repo.create_head("trunk")
    repo.create_remote("origin", "https://example.invalid/repo.git")
    repo.git.update_ref("refs/remotes/origin/trunk", "trunk")
    repo.git.symbolic_ref("refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")

    assert plonk._canonical_trunk_ref(repo) == "origin/trunk", (
        "expected remote default branch to take precedence over local main"
    )


def test_soft_mode_loads_only_worktree_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Soft mode should avoid trunk ref resolution and global cwd changes."""
    worktrees_root = tmp_path / "repo.worktrees"
    stanzas = [{"worktree": worktrees_root / "issue-123-fix"}]

    monkeypatch.setattr(plonk.helpers, "_find_repo", lambda _prefix: SimpleNamespace())
    monkeypatch.setattr(
        plonk.helpers, "_parse_worktree_porcelain", lambda _repo: stanzas
    )
    monkeypatch.setattr(
        plonk.helpers,
        "_main_worktree_path_from_list",
        lambda _stanzas, _prefix: tmp_path / "repo",
    )
    monkeypatch.setattr(plonk.donkey, "_worktrees_root", lambda _home: worktrees_root)
    monkeypatch.setattr(
        plonk,
        "_load_plonk_context",
        lambda: pytest.fail("soft mode should not load trunk cleanup context"),
    )
    monkeypatch.setattr(
        plonk,
        "_canonical_trunk_ref",
        lambda _repo: pytest.fail("soft mode should not resolve trunk history"),
    )
    monkeypatch.setattr(
        plonk.os,
        "chdir",
        lambda _path: pytest.fail("soft mode should not mutate cwd"),
    )

    exit_code = plonk.run_git_plonk(soft=True)

    assert exit_code == 0, "expected soft git plonk to succeed"
    assert "git-plonk: mode=soft" in capsys.readouterr().out, (
        "expected soft-mode summary output"
    )


def test_dry_run_soft_mode_reports_targets_without_removing_them(
    tmp_path: Path,
) -> None:
    """Dry-run soft mode should inspect generated paths without deleting them."""
    worktrees_root = tmp_path / "repo.worktrees"
    worktree_path = worktrees_root / "issue-123-fix"
    target_path = worktree_path / "target"
    target_path.mkdir(parents=True)

    result = plonk._run_soft(
        [{"worktree": worktree_path}],
        worktrees_root,
        dry_run=True,
    )

    assert result.mode is plonk._PlonkMode.SOFT, "expected soft dry-run mode"
    assert result.is_dry_run, "expected dry-run result marker"
    assert result.cleaned_paths == (target_path,), "expected planned target removal"
    assert target_path.is_dir(), "expected dry run to leave generated path intact"


class _FailingGitAdapter:
    """Git adapter double that plans cleanup but refuses to mutate.

    Dry-run cleanup must reuse candidate discovery while skipping destructive
    Git APIs, so ``remove_worktree`` and ``delete_branch`` fail the test if the
    planner ever invokes them. The completion marker is configurable so callers
    can pin it to the candidate branch under test.
    """

    def __init__(self, marker: str = "Merge pull request (#123)") -> None:
        self._marker = marker

    def history_messages(self, ref: str) -> typ.Iterator[str]:
        assert ref == "main", "expected configured trunk ref"
        yield self._marker

    def remove_worktree(self, worktree_path: Path) -> None:
        pytest.fail(f"dry run should not remove worktree {worktree_path}")

    def delete_branch(self, branch_name: str) -> None:
        pytest.fail(f"dry run should not delete branch {branch_name}")


@pytest.mark.parametrize(
    ("mode", "expected_branches"),
    [
        (plonk._PlonkMode.DEFAULT, ()),
        (plonk._PlonkMode.HARD, ("issue-123-fix",)),
    ],
)
def test_dry_run_completed_mode_reports_plans_without_mutating(
    mode: plonk._PlonkMode,
    expected_branches: tuple[str, ...],
) -> None:
    """Dry-run completed cleanup should plan work without destructive Git APIs."""
    candidate = plonk._PlonkCandidate(
        branch_name="issue-123-fix",
        worktree_path=Path("/repo.worktrees/issue-123-fix"),
        marker="(#123)",
    )
    context = plonk._PlonkContext(
        repo_home=typ.cast("Repo", SimpleNamespace()),
        stanzas=[
            {
                "branch": "refs/heads/issue-123-fix",
                "worktree": candidate.worktree_path,
            }
        ],
        worktrees_root=Path("/repo.worktrees"),
        trunk_ref="main",
        invoking_worktree=None,
    )

    result = plonk._run_completed_cleanup(
        context,
        mode,
        git_adapter=typ.cast("plonk._GitWorktreeAdapter", _FailingGitAdapter()),
        dry_run=True,
    )

    assert result.is_dry_run, "expected dry-run result marker"
    assert result.removed_worktrees == (candidate.worktree_path,), (
        "expected planned worktree removal"
    )
    assert result.removed_branches == expected_branches, (
        f"expected planned branch deletion for {mode.value} mode"
    )


@given(
    mode=st.sampled_from((
        plonk._PlonkMode.DEFAULT,
        plonk._PlonkMode.HARD,
    )),
    issue_number=st.integers(min_value=1, max_value=999_999),
)
def test_dry_run_modes_never_mutate_and_report_mode_specific_plans(
    mode: plonk._PlonkMode,
    issue_number: int,
) -> None:
    """Completed-cleanup dry runs should report plans without mutating adapters.

    This pins the pure policy invariant for the ``DEFAULT`` and ``HARD`` modes;
    ``SOFT`` filesystem planning is covered separately by
    ``test_dry_run_soft_mode_reports_targets_without_removing_them``.
    """
    branch_name = f"issue-{issue_number}-fix"
    candidate = plonk._PlonkCandidate(
        branch_name=branch_name,
        worktree_path=Path(f"/repo.worktrees/{branch_name}"),
        marker=f"(#{issue_number})",
    )
    context = plonk._PlonkContext(
        repo_home=typ.cast("Repo", SimpleNamespace()),
        stanzas=[
            {
                "branch": f"refs/heads/{branch_name}",
                "worktree": candidate.worktree_path,
            }
        ],
        worktrees_root=Path("/repo.worktrees"),
        trunk_ref="main",
        invoking_worktree=None,
    )

    result = plonk._run_completed_cleanup(
        context,
        mode,
        git_adapter=typ.cast(
            "plonk._GitWorktreeAdapter",
            _FailingGitAdapter(f"Merge pull request (#{issue_number})"),
        ),
        dry_run=True,
    )

    expected_branches = (branch_name,) if mode is plonk._PlonkMode.HARD else ()
    assert result.mode is mode, "expected completed dry-run mode to be preserved"
    assert result.is_dry_run, "expected dry-run result marker"
    assert result.removed_worktrees == (candidate.worktree_path,), (
        "expected planned worktree removal"
    )
    assert result.removed_branches == expected_branches, (
        "expected branch plans only in hard dry-run mode"
    )


def test_dry_run_summary_reports_planned_actions() -> None:
    """Dry-run summaries should name planned work instead of completed removals."""
    result = plonk._PlonkResult(
        mode=plonk._PlonkMode.HARD,
        is_dry_run=True,
        removed_worktrees=(Path("/repo.worktrees/issue-123-fix"),),
        removed_branches=("issue-123-fix",),
    )

    assert plonk._render_summary(result) == (
        "git-plonk: mode=hard dry-run\n"
        "Planned worktree removals:\n"
        "- /repo.worktrees/issue-123-fix\n"
        "Planned branch deletions:\n"
        "- issue-123-fix"
    ), "expected dry-run summary to report planned actions"


def test_soft_summary_reports_nothing_to_clean_after_inspection() -> None:
    """Soft mode should distinguish empty worktrees from no matching worktrees."""
    result = plonk._PlonkResult(
        mode=plonk._PlonkMode.SOFT,
        inspected_worktrees=2,
    )

    assert plonk._render_summary(result) == (
        "git-plonk: mode=soft\nNo generated paths to clean in git donkey worktrees."
    ), "expected inspected soft cleanup to report nothing to clean"


def test_soft_summary_reports_no_matching_worktrees_when_none_inspected() -> None:
    """Soft mode should keep the generic no-match message with no worktrees."""
    result = plonk._PlonkResult(mode=plonk._PlonkMode.SOFT)

    assert plonk._render_summary(result) == (
        "git-plonk: mode=soft\nNo matching git donkey worktrees found."
    ), "expected soft cleanup with no worktrees to report no matches"


def test_summary_rendering_matches_snapshot(snapshot: SnapshotAssertion) -> None:
    """Command summaries should stay stable for reviewable CLI output."""
    result = plonk._PlonkResult(
        mode=plonk._PlonkMode.HARD,
        removed_worktrees=(
            Path("/repo.worktrees/issue-123-fix"),
            Path("/repo.worktrees/road-1-2-3a-4-task"),
        ),
        removed_branches=("issue-123-fix", "road-1-2-3a-4-task"),
        cleaned_paths=(Path("/repo.worktrees/issue-123-fix/target"),),
    )

    assert plonk._render_summary(result) == snapshot, (
        "expected summary rendering to match snapshot"
    )
