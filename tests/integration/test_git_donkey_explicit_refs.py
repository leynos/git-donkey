"""Real-repository tests for explicit ``git donkey`` bases of every form.

A caller reading ``git status`` sees ``origin/main``, so passing exactly that
name must root a new branch on the remote tip. These tests cover the forms an
explicit base now accepts — a remote-qualified branch, a canonical
remote-tracking ref, a tag, and a commit ID — alongside the errors, the
precedence rules that keep every previously accepted input working, and the
guarantee that a failed base leaves neither a branch nor a worktree behind.
"""

from __future__ import annotations

import typing as typ

import pytest
from git import Repo

from git_donkey import donkey
from tests.integration.conftest import _setup_repo
from tests.integration.donkey_helpers import (
    DonkeyScenario,
    leave_base_behind_remote,
    new_scenario,
    run_donkey,
    seed_repo,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

_REJECTED_EXIT_CODE = 1

# Each unresolvable base and the one message that identifies it.
_UNRESOLVABLE_BASES = {
    "origin/nope": "base 'origin/nope' not found",
    "refs/remotes/origin/nope": "remote base ref not found",
    "no-such-tag": (
        "base branch 'no-such-tag' not found locally or on 'origin/no-such-tag'"
    ),
}


def _reject_prompt(_question: str) -> bool:
    """Fail if a run that must not update a base asks to do so."""
    pytest.fail("this run must not prompt to update a base checkout")


def _tag_commit(scenario: DonkeyScenario, tag: str) -> str:
    """Tag the current commit of ``scenario``'s checkout and return its ID."""
    commit = scenario.repo.head.commit.hexsha
    scenario.repo.create_tag(tag)
    return commit


def _stale_local_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> DonkeyScenario:
    """Create a repository whose local main trails the remote by one commit."""
    scenario = new_scenario(tmp_path, monkeypatch, "feature/from-remote-tip")
    leave_base_behind_remote(scenario)
    return scenario


@pytest.mark.parametrize("base", ["origin/main", "refs/remotes/origin/main"])
def test_remote_qualified_base_roots_the_new_branch_on_the_remote_tip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    base: str,
) -> None:
    """A qualified base selects the remote tip, not the stale local branch."""
    scenario = _stale_local_main(tmp_path, monkeypatch)
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", _reject_prompt)

    run_donkey(scenario, capsys, base)

    assert scenario.exit_code == 0, "a qualified base is accepted"
    assert scenario.worktree_head() == scenario.remote_tip, (
        "the new branch starts at the remote tip the base names"
    )
    assert scenario.repo.head.commit.hexsha == scenario.local_tip, (
        "the stale local base is left untouched"
    )
    assert "origin/origin" not in scenario.stderr, (
        "the doubled-remote error never appears"
    )


def test_remote_qualified_base_creates_no_local_tracking_branch_of_the_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A qualified base resolves in place, without localizing the base branch."""
    local_path, remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)
    repo.git.checkout("-b", "release/1.2")
    seed_repo(repo, "release.txt", "release work")
    repo.remote("origin").push("release/1.2")
    remote_tip = repo.head.commit.hexsha
    repo.git.checkout("main")
    repo.git.branch("-D", "release/1.2")
    scenario = DonkeyScenario(
        local_path=local_path,
        remote_path=remote_path,
        branch="feature/from-release",
    )
    scenario.remote_tip = remote_tip
    monkeypatch.chdir(local_path)

    run_donkey(scenario, capsys, "origin/release/1.2")

    assert scenario.exit_code == 0, "a qualified non-default branch is accepted"
    assert scenario.worktree_head() == remote_tip, (
        "the new branch starts at the named remote branch"
    )
    assert "release/1.2" not in scenario.repo.heads, (
        "resolving a qualified base creates no local copy of that base"
    )


def test_tag_base_roots_the_new_branch_at_the_tagged_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A tag names the commit the new branch starts from."""
    scenario = new_scenario(tmp_path, monkeypatch, "feature/from-tag")
    tagged = _tag_commit(scenario, "v1.0")
    seed_repo(scenario.repo, "later.txt", "later work")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", _reject_prompt)

    run_donkey(scenario, capsys, "v1.0")

    assert scenario.exit_code == 0, "a tag is an accepted base"
    assert scenario.worktree_head() == tagged, (
        "the new branch starts at the tagged commit, not the branch tip"
    )


@pytest.mark.parametrize("form", ["full", "abbreviated"])
def test_commit_id_base_roots_the_new_branch_at_that_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    form: str,
) -> None:
    """A full or abbreviated commit ID names the start point."""
    scenario = new_scenario(tmp_path, monkeypatch, "feature/from-commit")
    start = scenario.repo.head.commit.hexsha
    seed_repo(scenario.repo, "later.txt", "later work")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", _reject_prompt)
    base = start[:8] if form == "abbreviated" else start

    run_donkey(scenario, capsys, base)

    assert scenario.exit_code == 0, "a commit ID is an accepted base"
    assert scenario.worktree_head() == start, (
        "the new branch starts at the commit the ID names"
    )


def test_non_principal_remote_qualified_base_uses_that_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A qualified base naming another configured remote is accepted."""
    local_path, remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)
    # `origin` stays the first configured (principal) remote. The second
    # remote gets its own store and a tip that differs from `origin/main`.
    upstream_path = tmp_path / "upstream.git"
    Repo.init(upstream_path, bare=True)
    repo.create_remote("upstream", upstream_path.as_posix())
    seed_repo(repo, "other.txt", "other remote work")
    repo.remote("upstream").push("main")
    remote_tip = repo.head.commit.hexsha
    repo.git.reset("--hard", "HEAD~1")
    local_tip = repo.head.commit.hexsha
    scenario = DonkeyScenario(
        local_path=local_path,
        remote_path=remote_path,
        branch="feature/from-upstream",
    )
    scenario.remote_tip = remote_tip
    scenario.local_tip = local_tip
    monkeypatch.chdir(local_path)
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", _reject_prompt)
    assert repo.remotes[0].name == "origin", "origin remains the principal remote"
    assert repo.commit("origin/main").hexsha != repo.commit("upstream/main").hexsha, (
        "the two remotes must offer different tips for the assertion to bite"
    )

    run_donkey(scenario, capsys, "upstream/main")

    assert scenario.exit_code == 0, "a non-principal remote may qualify the base"
    assert scenario.worktree_head() == remote_tip, (
        "the new branch starts at that remote's tip"
    )
    assert scenario.worktree_head() != repo.commit("origin/main").hexsha, (
        "the base is resolved from the named remote, not the principal one"
    )
    assert scenario.repo.head.commit.hexsha == local_tip, (
        "the stale local base is left untouched"
    )


@pytest.mark.parametrize("base", sorted(_UNRESOLVABLE_BASES))
def test_unresolvable_base_fails_without_creating_anything(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    base: str,
) -> None:
    """An unresolvable base names itself once and leaves the repository alone."""
    scenario = new_scenario(tmp_path, monkeypatch, "feature/bad-base")

    run_donkey(scenario, capsys, base)

    assert scenario.exit_code == _REJECTED_EXIT_CODE, "an unknown base is an error"
    assert _UNRESOLVABLE_BASES[base] in scenario.stderr, (
        "the error identifies the base once"
    )
    assert "origin/origin" not in scenario.stderr, (
        "a qualified base is never reported with a doubled remote"
    )
    assert scenario.branch not in scenario.repo.heads, "no branch is created"
    assert not scenario.worktree_path().exists(), "no worktree is created"


def test_option_shaped_base_is_rejected_instead_of_listing_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An option-like base is resolved as a revision, never as a Git option."""
    scenario = new_scenario(tmp_path, monkeypatch, "feature/option-base")

    run_donkey(scenario, capsys, "--all")

    assert scenario.exit_code == _REJECTED_EXIT_CODE, "an option-shaped base fails"
    assert "--all" in scenario.stderr, "the error quotes the supplied base"
    assert scenario.branch not in scenario.repo.heads, "no branch is created"


def test_local_branch_wins_over_a_colliding_remote_qualified_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A literal local branch named ``origin/main`` keeps its own commits."""
    scenario = new_scenario(tmp_path, monkeypatch, "feature/from-literal")
    repo = scenario.repo
    repo.git.checkout("-b", "origin/main")
    seed_repo(repo, "literal.txt", "literal branch work")
    literal_tip = repo.head.commit.hexsha
    repo.git.checkout("main")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", _reject_prompt)

    run_donkey(scenario, capsys, "origin/main")

    assert scenario.exit_code == 0, "the literal branch name is accepted"
    assert scenario.worktree_head() == literal_tip, (
        "an existing local branch keeps its literal meaning"
    )
    remote_tip = scenario.repo.commit("refs/remotes/origin/main").hexsha
    assert scenario.worktree_head() != remote_tip, (
        "the literal branch is not reinterpreted as the remote-tracking ref"
    )


def test_reused_branch_ignores_the_supplied_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A base affects new branches only, even when it names a remote tip."""
    scenario = _stale_local_main(tmp_path, monkeypatch)
    repo = scenario.repo
    repo.git.checkout("-b", scenario.branch)
    seed_repo(repo, "feature.txt", "feature work")
    existing_tip = repo.head.commit.hexsha
    repo.remote("origin").push(scenario.branch)
    repo.git.checkout("main")

    run_donkey(scenario, capsys, "origin/main")

    assert scenario.exit_code == 0, "the existing branch is reused"
    assert scenario.worktree_head() == existing_tip, (
        "the reused branch keeps its own tip instead of adopting the base"
    )


def test_remote_qualified_base_without_an_upstream_still_resolves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A remote-tracking ref needs no local upstream configuration."""
    scenario = new_scenario(tmp_path, monkeypatch, "feature/no-upstream")
    assert scenario.repo.active_branch.tracking_branch() is None, (
        "the seeded local main has no upstream to consult"
    )
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", _reject_prompt)

    run_donkey(scenario, capsys, "origin/main")

    assert scenario.exit_code == 0, "the base resolves from the remote ref alone"
    assert scenario.worktree_head() == scenario.repo.commit("origin/main").hexsha, (
        "the new branch starts at the remote-tracking commit"
    )
    assert scenario.worktree_repo().active_branch.tracking_branch() is None, (
        "a new branch does not inherit tracking from a remote-qualified base"
    )


def test_canonical_remote_revision_expression_is_resolved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A canonical remote ref with an ancestor suffix is a valid base."""
    scenario = new_scenario(tmp_path, monkeypatch, "feature/from-parent")
    repo = scenario.repo
    seed_repo(repo, "second.txt", "second commit")
    repo.remote("origin").push("main")
    repo.remote("origin").fetch()
    expected = repo.commit("refs/remotes/origin/main~1").hexsha
    scenario.remote_tip = repo.commit("refs/remotes/origin/main").hexsha
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", _reject_prompt)

    run_donkey(scenario, capsys, "refs/remotes/origin/main~1")

    assert scenario.exit_code == 0, "a canonical revision expression is accepted"
    assert scenario.worktree_head() == expected, (
        "the expression resolves to the commit it names"
    )
    assert scenario.worktree_head() != scenario.remote_tip, (
        "the ancestor, not the ref tip, supplies the start point"
    )


def test_canonical_remote_ref_that_does_not_exist_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A canonical principal-remote ref with no commit behind it still fails."""
    scenario = new_scenario(tmp_path, monkeypatch, "feature/missing-ref")

    run_donkey(scenario, capsys, "refs/remotes/origin/nope")

    assert scenario.exit_code == _REJECTED_EXIT_CODE, "an unknown ref is an error"
    assert "remote base ref not found" in scenario.stderr, (
        "the error names the missing remote ref form"
    )
    assert scenario.branch not in scenario.repo.heads, "no branch is created"


def test_head_expression_resolves_in_the_calling_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``HEAD~1`` names the caller's history, even from a linked worktree."""
    scenario = new_scenario(tmp_path, monkeypatch, "feature/from-caller-head")
    repo = scenario.repo
    first = repo.head.commit.hexsha
    seed_repo(repo, "second.txt", "second commit")
    second = repo.head.commit.hexsha
    caller_path = tmp_path / "caller-worktree"
    repo.git.worktree("add", "-b", "caller/branch", str(caller_path), second)
    # Advance the caller alone, so its HEAD and the anchor's HEAD differ and
    # an anchor-relative resolution would choose a different commit.
    caller_repo = Repo(caller_path)
    seed_repo(caller_repo, "third.txt", "third commit")
    third = caller_repo.head.commit.hexsha
    repo.git.checkout("-b", "anchor/side", first)
    monkeypatch.chdir(caller_path)
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", _reject_prompt)
    assert first != second != third, "the three commits are distinct"

    run_donkey(scenario, capsys, "HEAD~1")

    assert scenario.exit_code == 0, "a revision expression is accepted"
    assert scenario.worktree_head() == second, (
        "the expression resolves against the calling checkout's HEAD"
    )
    assert scenario.worktree_head() != first, (
        "the anchor's HEAD does not decide the start point"
    )


def test_canonical_tag_base_is_not_shadowed_by_a_branch_of_that_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A fully qualified tag is used as written, whatever else spells its name."""
    scenario = new_scenario(tmp_path, monkeypatch, "feature/from-tag-ref")
    repo = scenario.repo
    tagged = _tag_commit(scenario, "v1.0")
    seed_repo(repo, "later.txt", "later work")
    # A local branch whose literal name spells the same ref must not win.
    repo.git.branch("refs/tags/v1.0", "HEAD")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", _reject_prompt)

    run_donkey(scenario, capsys, "refs/tags/v1.0")

    assert scenario.exit_code == 0, "a canonical tag ref is accepted"
    assert scenario.worktree_head() == tagged, (
        "the tag, not the shadowing branch, supplies the start point"
    )


def test_absent_canonical_ref_is_not_satisfied_by_a_shadowing_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing canonical ref is rejected, not resolved in another namespace."""
    scenario = new_scenario(tmp_path, monkeypatch, "feature/absent-ref")
    repo = scenario.repo
    # Git's revision resolver would otherwise find this branch when asked for
    # the absent refs/tags/absent.
    repo.git.branch("refs/tags/absent", "HEAD")

    run_donkey(scenario, capsys, "refs/tags/absent")

    assert scenario.exit_code == _REJECTED_EXIT_CODE, "the absent ref is an error"
    assert "base 'refs/tags/absent' not found" in scenario.stderr, (
        "the error names the missing canonical ref"
    )
    assert scenario.branch not in scenario.repo.heads, "no branch is created"
    assert not scenario.worktree_path().exists(), "no worktree is created"
