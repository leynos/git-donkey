"""Real Git tests for explicit rebase and fast-forward-only base updates."""

from __future__ import annotations

import typing as typ

import pytest
from git import Repo

from git_donkey import donkey
from tests.integration.conftest import _setup_repo
from tests.integration.donkey_helpers import seed_repo
from tests.output_assertions import (
    GIT_ERROR_PLACEHOLDER,
    require_stream,
    stream,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


def _behind_repo(tmp_path: Path) -> Repo:
    """Create a local main one commit behind its remote counterpart."""
    local_path, _remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)
    seed_repo(repo, "upstream.txt", "upstream change")
    repo.remote("origin").push("main")
    repo.git.reset("--hard", "HEAD~1")
    return repo


def _reject_prompt(_question: str) -> bool:
    """Fail if creation from a commit-ish base asks to update a checkout."""
    pytest.fail("a commit-ish base has no local branch to update")


def test_pull_rebase_preserves_local_commits_on_explicit_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit rebase incorporates remote commits and replays local work."""
    repo = _behind_repo(tmp_path)
    seed_repo(repo, "local.txt", "local change")
    old_tip = repo.head.commit.hexsha
    monkeypatch.chdir(repo.working_tree_dir or ".")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", lambda *_: True)

    assert (
        donkey.run_git_donkey(
            "feature/rebased", ".", options=donkey._PullOptions(pull_rebase=True)
        )
        == 0
    ), "an accepted rebase pull creates the worktree"

    assert repo.head.commit.hexsha != old_tip, "the rebase moves the local base forward"
    assert (
        repo.git.merge_base("main", "origin/main") == repo.commit("origin/main").hexsha
    ), "the local base now contains the remote commit"
    assert repo.git.show("main:local.txt") == "local change", (
        "the local commit is replayed"
    )
    assert repo.git.show("main:upstream.txt") == "upstream change", (
        "the remote commit is incorporated"
    )
    assert repo.commit("feature/rebased").hexsha == repo.head.commit.hexsha, (
        "the new branch starts from the rebased base"
    )


def test_pull_ff_refuses_divergence_even_with_rebase_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fast-forward mode must neither merge nor rebase divergent local work."""
    repo = _behind_repo(tmp_path)
    seed_repo(repo, "local.txt", "local change")
    original_tip = repo.head.commit.hexsha
    repo.git.config("pull.rebase", "true")
    repo.git.config("pull.ff", "false")
    monkeypatch.chdir(repo.working_tree_dir or ".")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", lambda *_: True)

    with pytest.raises(SystemExit) as excinfo:
        donkey.run_git_donkey(
            "feature/ff", ".", options=donkey._PullOptions(pull_ff=True)
        )

    assert excinfo.value.code == 1, "divergence under --ff-only is an error"
    captured = capsys.readouterr()
    assert not captured.out, "a failed update prints nothing on stdout"
    require_stream(
        captured.err,
        stream(
            "Using remote: origin",
            f"Updating existing worktree at: {repo.working_tree_dir}",
            f"git-donkey: 💀 update failed (pull --ff-only): {GIT_ERROR_PLACEHOLDER}",
        ),
        context="git donkey stderr",
    )
    assert repo.head.commit.hexsha == original_tip, (
        "the diverged local base is left untouched"
    )
    assert "feature/ff" not in repo.heads, "no branch is created when the update fails"
    assert not repo.is_dirty(untracked_files=True), (
        "the failed attempt leaves the working tree clean"
    )


@pytest.mark.parametrize(
    "options",
    [donkey._PullOptions(pull_rebase=True), donkey._PullOptions(pull_ff=True)],
)
def test_opt_in_never_pulls_base_into_unrelated_primary_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    options: donkey._PullOptions,
) -> None:
    """An unowned base must not be pulled into the primary worktree's branch."""
    repo = _behind_repo(tmp_path)
    repo.git.checkout("-b", "unrelated")
    seed_repo(repo, "unrelated.txt", "unrelated change")
    original_tip = repo.head.commit.hexsha
    monkeypatch.chdir(repo.working_tree_dir or ".")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", lambda *_: True)

    with pytest.raises(SystemExit) as excinfo:
        donkey.run_git_donkey("feature/unsafe", "main", options=options)

    assert excinfo.value.code == 1, "a base held by no worktree is an error"
    captured = capsys.readouterr()
    assert not captured.out, "a refused update prints nothing on stdout"
    require_stream(
        captured.err,
        stream(
            "Using remote: origin",
            "git-donkey: 💀 cannot pull 'main': it is not checked out in a "
            "worktree; check out that base explicitly or omit the pull option",
        ),
        context="git donkey stderr",
    )
    assert repo.active_branch.name == "unrelated", (
        "the primary checkout keeps its branch"
    )
    assert repo.head.commit.hexsha == original_tip, "the primary checkout is not moved"
    assert "feature/unsafe" not in repo.heads, (
        "no branch is created when the base is unowned"
    )


@pytest.mark.parametrize(
    "options",
    [donkey._PullOptions(pull_rebase=True), donkey._PullOptions(pull_ff=True)],
)
def test_declined_prompt_preserves_explicit_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    options: donkey._PullOptions,
) -> None:
    """Declining an opted-in update keeps the existing base commit."""
    repo = _behind_repo(tmp_path)
    original_tip = repo.head.commit.hexsha
    monkeypatch.chdir(repo.working_tree_dir or ".")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", lambda *_: False)

    assert donkey.run_git_donkey("feature/declined", ".", options=options) == 0, (
        "declining the prompt still creates the worktree"
    )

    assert repo.head.commit.hexsha == original_tip, "the declined base is not updated"
    assert repo.commit("feature/declined").hexsha == original_tip, (
        "the new branch starts at the declined base"
    )


@pytest.mark.parametrize(
    "options",
    [donkey._PullOptions(pull_rebase=True), donkey._PullOptions(pull_ff=True)],
)
def test_pull_updates_linked_base_and_leaves_primary_alone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    options: donkey._PullOptions,
) -> None:
    """Pulling targets the base's linked worktree, not the calling checkout."""
    repo = _behind_repo(tmp_path)
    repo.git.checkout("-b", "unrelated")
    seed_repo(repo, "unrelated.txt", "unrelated change")
    original_tip = repo.head.commit.hexsha
    base_path = tmp_path / "base-worktree"
    repo.git.worktree("add", str(base_path), "main")
    monkeypatch.chdir(repo.working_tree_dir or ".")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", lambda *_: True)

    assert donkey.run_git_donkey("feature/linked", "main", options=options) == 0, (
        "the base is updated in its own worktree"
    )

    assert repo.active_branch.name == "unrelated", (
        "the calling checkout keeps its branch"
    )
    assert repo.head.commit.hexsha == original_tip, "the calling checkout is not moved"
    assert Repo(base_path).head.commit.hexsha == repo.commit("origin/main").hexsha, (
        "the linked base worktree is updated"
    )
    assert repo.commit("feature/linked").hexsha == repo.commit("origin/main").hexsha, (
        "the new branch starts from the updated base"
    )


def test_pull_ff_updates_the_local_base_named_by_a_remote_qualified_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A qualified parent behaves like an omitted base for pull preflight."""
    repo = _behind_repo(tmp_path)
    original_tip = repo.head.commit.hexsha
    monkeypatch.chdir(repo.working_tree_dir or ".")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", lambda *_: True)

    assert (
        donkey.run_git_donkey(
            "feature/qualified",
            "origin/main",
            options=donkey._PullOptions(pull_ff=True),
        )
        == 0
    ), "an accepted update still creates the worktree"

    assert repo.head.commit.hexsha == repo.commit("origin/main").hexsha, (
        "the local base named by the parent is fast-forwarded"
    )
    assert repo.head.commit.hexsha != original_tip, "the local base moved forward"
    assert repo.commit("feature/qualified").hexsha == repo.head.commit.hexsha, (
        "the new branch starts at the updated remote tip"
    )
    require_stream(
        capsys.readouterr().err,
        stream(
            "Using remote: origin",
            f"Updating existing worktree at: {repo.working_tree_dir}",
            "Creating new branch 'feature/qualified' from "
            "'refs/remotes/origin/main' in a new worktree",
        ),
        context="git donkey stderr",
    )


@pytest.mark.parametrize("parent_kind", ["tag", "commit"])
def test_commit_ish_parent_skips_the_update_and_the_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    parent_kind: str,
) -> None:
    """A tag or commit ID has no branch to update and must not prompt."""
    repo = _behind_repo(tmp_path)
    local_tip = repo.head.commit.hexsha
    remote = repo.commit("origin/main")
    repo.create_tag("release-tag", ref=remote.hexsha)
    parent = "release-tag" if parent_kind == "tag" else remote.hexsha
    monkeypatch.chdir(repo.working_tree_dir or ".")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", _reject_prompt)

    assert (
        donkey.run_git_donkey(
            "feature/commit-ish",
            parent,
            options=donkey._PullOptions(pull_ff=True),
        )
        == 0
    ), "a tag or commit base creates the worktree without an update"

    assert repo.head.commit.hexsha == local_tip, "the local base is not updated"
    assert repo.commit("feature/commit-ish").hexsha == remote.hexsha, (
        "the new branch starts at the commit the base names"
    )
    require_stream(
        capsys.readouterr().err,
        stream(
            "Using remote: origin",
            f"Creating new branch 'feature/commit-ish' from '{parent}' in a "
            "new worktree",
        ),
        context="git donkey stderr",
    )


def test_unresolvable_qualified_parent_fails_before_prompting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unknown qualified base is reported once, before any prompt or write."""
    repo = _behind_repo(tmp_path)
    monkeypatch.chdir(repo.working_tree_dir or ".")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", _reject_prompt)

    with pytest.raises(SystemExit) as excinfo:
        donkey.run_git_donkey(
            "feature/invalid-base",
            "origin/missing",
            options=donkey._PullOptions(pull_ff=True),
        )

    assert excinfo.value.code == 1, "an unknown parent is a base error"
    require_stream(
        capsys.readouterr().err,
        stream(
            "Using remote: origin",
            "git-donkey: 💀 base 'origin/missing' not found",
        ),
        context="git donkey stderr",
    )
    assert "feature/invalid-base" not in repo.heads, "no branch is created"


def test_pull_ff_updates_a_canonical_local_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A fully qualified local branch is still a local base worth updating."""
    repo = _behind_repo(tmp_path)
    original_tip = repo.head.commit.hexsha
    monkeypatch.chdir(repo.working_tree_dir or ".")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", lambda *_: True)

    assert (
        donkey.run_git_donkey(
            "feature/canonical-local",
            "refs/heads/main",
            options=donkey._PullOptions(pull_ff=True),
        )
        == 0
    ), "an accepted update still creates the worktree"

    assert repo.head.commit.hexsha == repo.commit("origin/main").hexsha, (
        "the fully qualified local base is fast-forwarded"
    )
    assert repo.head.commit.hexsha != original_tip, "the local base moved forward"
    assert repo.commit("feature/canonical-local").hexsha == repo.head.commit.hexsha, (
        "the new branch starts at the updated base"
    )
    require_stream(
        capsys.readouterr().err,
        stream(
            "Using remote: origin",
            f"Updating existing worktree at: {repo.working_tree_dir}",
            "Creating new branch 'feature/canonical-local' from "
            "'refs/heads/main' in a new worktree",
        ),
        context="git donkey stderr",
    )


def test_pull_ff_prompt_is_not_fooled_by_a_tag_shadowing_the_base_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A tag named like the base branch must not hide that it is behind."""
    repo = _behind_repo(tmp_path)
    original_tip = repo.head.commit.hexsha
    # The tag points at the remote tip, so a short-name comparison against
    # `main` would report nothing to do and skip the prompt.
    repo.create_tag("main", ref=repo.commit("origin/main").hexsha)
    monkeypatch.chdir(repo.working_tree_dir or ".")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", lambda *_: True)

    assert (
        donkey.run_git_donkey(
            "feature/tag-shadow",
            "origin/main",
            options=donkey._PullOptions(pull_ff=True),
        )
        == 0
    ), "the behind base is still updated and the worktree created"

    assert repo.commit("refs/heads/main").hexsha == repo.commit("origin/main").hexsha, (
        "the local branch is fast-forwarded despite the shadowing tag"
    )
    assert repo.head.commit.hexsha != original_tip, "the local base moved forward"
    assert repo.commit("feature/tag-shadow").hexsha == repo.head.commit.hexsha, (
        "the new branch starts at the updated base"
    )
    require_stream(
        capsys.readouterr().err,
        stream(
            "Using remote: origin",
            f"Updating existing worktree at: {repo.working_tree_dir}",
            "Creating new branch 'feature/tag-shadow' from "
            "'refs/remotes/origin/main' in a new worktree",
        ),
        context="git donkey stderr",
    )


def test_canonical_tag_base_never_prompts_even_with_shadowing_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A canonical tag base has no branch to update, whatever else exists."""
    repo = _behind_repo(tmp_path)
    local_tip = repo.head.commit.hexsha
    remote_tip = repo.commit("origin/main").hexsha
    repo.create_tag("release-tag", ref=remote_tip)
    repo.git.branch("refs/tags/release-tag", remote_tip)
    repo.remote("origin").push(f"{remote_tip}:refs/heads/refs/tags/release-tag")
    repo.git.branch("-f", "refs/tags/release-tag", local_tip)
    assert repo.commit("refs/remotes/origin/refs/tags/release-tag").hexsha == (
        remote_tip
    ), "the shadowing remote branch must exist for the guard to matter"
    monkeypatch.chdir(repo.working_tree_dir or ".")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", _reject_prompt)

    assert (
        donkey.run_git_donkey(
            "feature/tag-ref",
            "refs/tags/release-tag",
            options=donkey._PullOptions(pull_ff=True),
        )
        == 0
    ), "a canonical tag base creates the worktree without prompting"

    assert repo.commit("feature/tag-ref").hexsha == remote_tip, (
        "the new branch starts at the tagged commit"
    )
    assert repo.commit("refs/heads/refs/tags/release-tag").hexsha == local_tip, (
        "the unrelated literal branch is left untouched"
    )
    require_stream(
        capsys.readouterr().err,
        stream(
            "Using remote: origin",
            "Creating new branch 'feature/tag-ref' from "
            "'refs/tags/release-tag' in a new worktree",
        ),
        context="git donkey stderr",
    )


def test_foreign_remote_base_never_prompts_despite_a_literal_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A non-principal remote base has no local update target, whatever exists."""
    repo = _behind_repo(tmp_path)
    remote_tip = repo.commit("origin/main").hexsha
    local_tip = repo.head.commit.hexsha
    # A literal branch and remote-tracking ref spelled like the base would let
    # an over-eager behind check find something to update.
    repo.create_remote("upstream", repo.remote("origin").url)
    repo.remote("upstream").fetch()
    repo.git.branch("refs/remotes/upstream/main", local_tip)
    repo.remote("upstream").push(f"{remote_tip}:refs/heads/refs/remotes/upstream/main")
    repo.remote("upstream").fetch()
    monkeypatch.chdir(repo.working_tree_dir or ".")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", _reject_prompt)

    assert (
        donkey.run_git_donkey(
            "feature/foreign-base",
            "refs/remotes/upstream/main",
            options=donkey._PullOptions(pull_ff=True),
        )
        == 0
    ), "a foreign-remote base creates the worktree without prompting"

    assert repo.head.commit.hexsha == local_tip, (
        "the shadowing local branch is left untouched"
    )
    assert repo.commit("feature/foreign-base").hexsha == remote_tip, (
        "the new branch starts at the foreign remote's tip"
    )
    require_stream(
        capsys.readouterr().err,
        stream(
            "Using remote: origin",
            "Creating new branch 'feature/foreign-base' from "
            "'refs/remotes/upstream/main' in a new worktree",
        ),
        context="git donkey stderr",
    )
