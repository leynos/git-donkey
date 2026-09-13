"""Behaviour-driven integration tests for git donkey branch reuse and conflicts.

The module binds scenarios from ``features/git_donkey_reuse.feature`` to real
temporary repositories built by ``tests.integration.donkey_helpers``. The steps
cover what happens when the requested branch already exists — locally, only on
the remote, or checked out in another worktree — along with the target-path and
detached-HEAD conflicts the workflow refuses, the nested directories a slashed
branch name produces, and the template overlay applied to a new worktree.

Which commit a new worktree is started from is covered by
``test_git_donkey_bases_bdd.py``.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

from pytest_bdd import given, parsers, scenarios, then, when

from git_donkey import slugs, templates
from tests.integration.conftest import _seed_repo
from tests.integration.donkey_helpers import (
    DonkeyScenario,
    new_scenario,
    run_donkey,
)

if typ.TYPE_CHECKING:
    import pytest
    from git import Repo

_NESTED_BRANCH = "feature/nested/task"
_OCCUPANT_CONTENT = "prior occupant"
_TEMPLATE_README = "README supplied by the template overlay\n"


def _worktree_stanza(repo: Repo, worktree_path: Path) -> list[str]:
    """Return the porcelain worktree stanza describing ``worktree_path``."""
    porcelain = repo.git.worktree("list", "--porcelain")
    target = worktree_path.resolve()
    for stanza in porcelain.split("\n\n"):
        lines = stanza.splitlines()
        if not lines:
            continue
        listed = Path(lines[0].removeprefix("worktree ")).resolve()
        if listed == target:
            return lines
    return []


@given(
    "a repository with an existing local branch behind a newer main",
    target_fixture="scenario",
)
def repository_with_existing_branch_and_newer_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> DonkeyScenario:
    """Publish a feature branch, then advance main past it."""
    scenario = new_scenario(tmp_path, monkeypatch, "feature/existing")
    repo = scenario.repo
    repo.git.checkout("-b", scenario.branch)
    _seed_repo(repo, "feature.txt", "feature work")
    repo.remote("origin").push(scenario.branch)
    scenario.local_tip = repo.head.commit.hexsha
    repo.git.checkout("main")
    _seed_repo(repo, "newer.txt", "newer main work")
    repo.remote("origin").push("main")
    return scenario


@given(
    "a repository with a branch that exists only on the remote",
    target_fixture="scenario",
)
def repository_with_remote_only_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> DonkeyScenario:
    """Publish a feature branch, then delete the local branch that created it."""
    scenario = new_scenario(tmp_path, monkeypatch, "feature/remote-only")
    repo = scenario.repo
    repo.git.checkout("-b", scenario.branch)
    _seed_repo(repo, "remote-only.txt", "remote-only work")
    repo.remote("origin").push(scenario.branch)
    scenario.remote_tip = repo.head.commit.hexsha
    repo.git.checkout("main")
    repo.git.branch("-D", scenario.branch)
    repo.remote("origin").fetch()
    return scenario


@given(
    "a repository with a local-only branch that was never pushed",
    target_fixture="scenario",
)
def repository_with_local_only_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> DonkeyScenario:
    """Create a feature branch and leave it unpublished."""
    scenario = new_scenario(tmp_path, monkeypatch, "feature/local-only")
    repo = scenario.repo
    repo.git.checkout("-b", scenario.branch)
    _seed_repo(repo, "local-only.txt", "local-only work")
    scenario.local_tip = repo.head.commit.hexsha
    repo.git.checkout("main")
    return scenario


@given(
    "a repository whose branch is checked out in another worktree",
    target_fixture="scenario",
)
def repository_with_branch_checked_out_elsewhere(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> DonkeyScenario:
    """Check the requested branch out in a worktree git donkey does not own."""
    scenario = new_scenario(tmp_path, monkeypatch, "feature/busy")
    scenario.repo.git.worktree(
        "add",
        "-b",
        scenario.branch,
        str(tmp_path / "elsewhere"),
        "main",
    )
    return scenario


@given(
    "a repository whose target worktree path is already occupied",
    target_fixture="scenario",
)
def repository_with_occupied_target_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> DonkeyScenario:
    """Pre-create the directory the new worktree would be placed in."""
    scenario = new_scenario(tmp_path, monkeypatch, "feature/taken")
    occupied = scenario.worktree_path()
    occupied.mkdir(parents=True)
    (occupied / "keep.txt").write_text(_OCCUPANT_CONTENT)
    return scenario


@given(
    "a repository with a detached HEAD in the calling checkout",
    target_fixture="scenario",
)
def repository_with_detached_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> DonkeyScenario:
    """Detach the calling checkout's HEAD from any branch."""
    scenario = new_scenario(tmp_path, monkeypatch, "feature/detached")
    scenario.repo.git.checkout("--detach")
    return scenario


@given(
    "a repository and a branch name containing nested path components",
    target_fixture="scenario",
)
def repository_for_a_nested_branch_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> DonkeyScenario:
    """Create a repository whose requested branch name carries two slashes."""
    return new_scenario(tmp_path, monkeypatch, _NESTED_BRANCH)


@given(
    "a repository with a template that overwrites a tracked file",
    target_fixture="scenario",
)
def repository_with_overwriting_template(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> DonkeyScenario:
    """Install a template overlay holding a README that differs from the tracked one."""
    scenario = new_scenario(tmp_path, monkeypatch, "feature/overlay")
    template_base = tmp_path / "templates"
    repo_slug = slugs.slug_dash_adler32(scenario.remote_path.as_posix())
    template_dir = template_base / repo_slug
    template_dir.mkdir(parents=True)
    (template_dir / "README.md").write_text(_TEMPLATE_README)
    monkeypatch.setattr(templates, "_get_template_base_dir", lambda: template_base)
    return scenario


@when("I run git donkey with the remote default base")
def run_donkey_with_remote_default_base(
    scenario: DonkeyScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run the workflow with an implicit base."""
    run_donkey(scenario, capsys)


@when("I run git donkey with main as the base")
def run_donkey_with_main_as_the_base(
    scenario: DonkeyScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run the workflow with main supplied as an explicit base."""
    run_donkey(scenario, capsys, "main")


@then("git donkey succeeds")
def git_donkey_succeeds(scenario: DonkeyScenario) -> None:
    """Assert the workflow reported success."""
    assert scenario.exit_code == 0, "expected git donkey to succeed"


@then(parsers.parse('git donkey fails with code {code:d} and reports "{message}"'))
def git_donkey_fails_reporting(
    scenario: DonkeyScenario,
    code: int,
    message: str,
) -> None:
    """Assert the workflow exited with ``code`` and explained itself on stderr."""
    assert scenario.exit_code == code, f"expected git donkey to exit with {code}"
    assert message in scenario.stderr, f"expected stderr to report {message!r}"


@then(parsers.parse('git donkey reports "{message}"'))
def git_donkey_reports(scenario: DonkeyScenario, message: str) -> None:
    """Assert the workflow wrote ``message`` to stderr."""
    assert message in scenario.stderr, f"expected stderr to report {message!r}"


@then("the new worktree starts at the existing branch tip")
def new_worktree_starts_at_the_existing_branch_tip(scenario: DonkeyScenario) -> None:
    """Assert the supplied base did not move the reused branch."""
    assert scenario.worktree_head() == scenario.local_tip, (
        "expected the reused branch to keep its own tip, not adopt the base's"
    )


@then("the local branch tracks its remote counterpart")
def local_branch_tracks_its_remote_counterpart(scenario: DonkeyScenario) -> None:
    """Assert a local tracking branch was created for the remote-only branch."""
    repo = scenario.repo
    assert scenario.branch in repo.heads, (
        "expected a local branch to be created for the remote-only branch"
    )
    tracking = scenario.worktree_repo().active_branch.tracking_branch()
    assert str(tracking) == f"origin/{scenario.branch}", (
        "expected the new local branch to track its remote counterpart"
    )


@then("the new worktree starts at the remote branch tip")
def new_worktree_starts_at_the_remote_branch_tip(scenario: DonkeyScenario) -> None:
    """Assert the remote-only branch supplied the worktree's commit."""
    assert scenario.worktree_head() == scenario.remote_tip, (
        "expected the worktree to start at the published branch tip"
    )


@then("no worktree is created")
def no_worktree_is_created(scenario: DonkeyScenario) -> None:
    """Assert the refused run left no worktree directory behind."""
    assert not scenario.worktree_path().exists(), (
        "expected the refused run to create no worktree"
    )


@then("no branch is created")
def no_branch_is_created(scenario: DonkeyScenario) -> None:
    """Assert the refused run created no local branch."""
    assert scenario.branch not in scenario.repo.heads, (
        "expected the refused run to create no branch"
    )


@then("the branch is left at its original tip")
def branch_is_left_at_its_original_tip(scenario: DonkeyScenario) -> None:
    """Assert the unpublished branch was neither moved nor deleted."""
    assert scenario.repo.commit(scenario.branch).hexsha == scenario.local_tip, (
        "expected the unpublished branch to keep its tip"
    )


@then("the occupying file is untouched")
def occupying_file_is_untouched(scenario: DonkeyScenario) -> None:
    """Assert the refused run left the occupied directory's content alone."""
    occupant = scenario.worktree_path() / "keep.txt"
    assert occupant.read_text() == _OCCUPANT_CONTENT, (
        "expected the occupying file to survive the refused run"
    )


@then("the worktree is registered for the nested branch at the nested path")
def worktree_is_registered_at_the_nested_path(scenario: DonkeyScenario) -> None:
    """Assert Git lists the nested branch against the nested worktree directory."""
    worktree_path = scenario.worktree_path()
    assert worktree_path.is_dir(), (
        "expected the slashed branch name to become nested directories"
    )
    stanza = _worktree_stanza(scenario.repo, worktree_path)
    assert stanza, f"expected git worktree list to name {worktree_path}"
    assert f"branch refs/heads/{scenario.branch}" in stanza, (
        "expected the nested worktree stanza to name the nested branch"
    )


@then("the new branch has no upstream")
def new_branch_has_no_upstream(scenario: DonkeyScenario) -> None:
    """Assert the new branch did not inherit tracking from its base."""
    assert scenario.worktree_repo().active_branch.tracking_branch() is None, (
        "expected the new branch not to track the remote default branch"
    )


@then("the new worktree holds the template content")
def new_worktree_holds_the_template_content(scenario: DonkeyScenario) -> None:
    """Assert the overlay overwrote the tracked file in the new worktree."""
    assert (scenario.worktree_path() / "README.md").read_text() == _TEMPLATE_README, (
        "expected the overlay to overwrite the tracked README"
    )


@then("the new worktree is dirty while the calling checkout stays clean")
def new_worktree_is_dirty_and_the_caller_is_clean(scenario: DonkeyScenario) -> None:
    """Assert the overlay's edit is uncommitted work in the new worktree only."""
    assert scenario.worktree_repo().is_dirty(), (
        "expected the overwritten tracked file to leave the worktree dirty"
    )
    assert not scenario.repo.is_dirty(untracked_files=True), (
        "expected the calling checkout to stay clean"
    )


scenarios("features/git_donkey_reuse.feature")
