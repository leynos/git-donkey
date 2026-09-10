"""Real Git tests for bounded workflow observability.

Each test drives ``git_donkey.donkey`` against a temporary repository with a
recording recorder installed, then asserts the emitted operation and outcome
sequence and that every recorded attribute stays inside the fixed vocabularies.
"""

from __future__ import annotations

import typing as typ

import pytest
from git import Repo

from git_donkey import donkey, slugs, templates
from tests.integration.conftest import _seed_repo, _setup_repo

if typ.TYPE_CHECKING:
    from pathlib import Path

    from tests.observability_helpers import RecordingRecorder


def _behind_repo(tmp_path: Path) -> Repo:
    """Create a local main one commit behind its remote counterpart."""
    local_path, _remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)
    _seed_repo(repo, "upstream.txt", "upstream change")
    repo.remote("origin").push("main")
    repo.git.reset("--hard", "HEAD~1")
    return repo


def _isolate_template_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point template lookup at an empty base directory and return it."""
    template_base = tmp_path / "templates"
    monkeypatch.setattr(templates, "_get_template_base_dir", lambda: template_base)
    return template_base


def _fail_prompt(*_args: object, **_kwargs: object) -> bool:
    """Fail the test when an unexpected prompt is shown."""
    msg = "a base that needs no update must not prompt"
    raise AssertionError(msg)


def _fail_apply(*_args: object, **_kwargs: object) -> typ.NoReturn:
    """Simulate a filesystem failure while copying the overlay."""
    msg = "simulated overlay failure"
    raise OSError(msg)


def test_default_base_run_records_every_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_recorder: RecordingRecorder,
) -> None:
    """An implicit-base run records each workflow step it took."""
    local_path, remote_path = _setup_repo(tmp_path)
    _isolate_template_base(tmp_path, monkeypatch)
    monkeypatch.chdir(local_path)

    exit_code = donkey.run_git_donkey("feature/observed")

    assert exit_code == 0, "the observed run still succeeds"
    assert recording_recorder.outcomes("remote_default_discovery") == ["success"], (
        "discovery reports success"
    )
    assert recording_recorder.outcomes("default_branch_fetch") == ["success"], (
        "the explicit fetch reports success"
    )
    assert recording_recorder.outcomes("base_update") == ["not_requested"], (
        "the default run requests no update"
    )
    assert recording_recorder.outcomes("worktree_creation") == ["started", "success"], (
        "creation reports its start and its result"
    )
    assert recording_recorder.outcomes("template_overlay") == ["unavailable"], (
        "an absent overlay reports itself unavailable"
    )
    assert recording_recorder.base_kinds("base_update") == [
        "implicit_remote_default"
    ], "the record names the remote default as the base"
    assert recording_recorder.span_operations() == [
        "remote_default_discovery",
        "default_branch_fetch",
        "worktree_creation",
    ], "the timed steps run in workflow order"
    assert recording_recorder.unbounded_values() == set(), (
        "every recorded value is drawn from a fixed vocabulary"
    )
    assert (
        recording_recorder.leaked_details((
            "feature/observed",
            str(local_path),
            str(remote_path),
        ))
        == set()
    ), "no branch name or path is recorded"


def test_explicit_base_records_the_selection_kind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_recorder: RecordingRecorder,
) -> None:
    """An explicit base records its kind and skips remote discovery."""
    local_path, _remote_path = _setup_repo(tmp_path)
    _isolate_template_base(tmp_path, monkeypatch)
    monkeypatch.chdir(local_path)

    exit_code = donkey.run_git_donkey("feature/explicit", "main")

    assert exit_code == 0, "an explicit base still succeeds"
    assert recording_recorder.outcomes("remote_default_discovery") == [], (
        "an explicit base needs no remote default"
    )
    assert recording_recorder.outcomes("default_branch_fetch") == [], (
        "an explicit base is not fetched"
    )
    assert recording_recorder.outcomes("base_update") == ["not_requested"], (
        "the default run requests no update"
    )
    assert recording_recorder.base_kinds("base_update") == ["explicit"], (
        "the record names the explicit base"
    )
    assert recording_recorder.span_operations() == ["worktree_creation"], (
        "only creation is timed for an explicit base"
    )
    assert recording_recorder.unbounded_values() == set(), (
        "every recorded value is drawn from a fixed vocabulary"
    )


def test_missing_advertised_default_records_the_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_recorder: RecordingRecorder,
) -> None:
    """A remote without an advertised default records that failure."""
    local_path, remote_path = _setup_repo(tmp_path)
    Repo(remote_path).git.symbolic_ref("HEAD", "refs/heads/missing")
    monkeypatch.chdir(local_path)

    with pytest.raises(SystemExit) as excinfo:
        donkey.run_git_donkey("feature/missing-default")

    assert excinfo.value.code == 1, "an unavailable default is still an error"
    assert recording_recorder.outcomes("remote_default_discovery") == ["failure"], (
        "discovery reports the failure"
    )
    assert recording_recorder.error_kinds("remote_default_discovery") == [
        "missing_advertised_default"
    ], "the failure class names the missing advertised default"
    assert recording_recorder.outcomes("worktree_creation") == [], (
        "the run stops before creating a worktree"
    )
    assert recording_recorder.unbounded_values() == set(), (
        "every recorded value is drawn from a fixed vocabulary"
    )
    assert (
        recording_recorder.leaked_details((
            "feature/missing-default",
            str(local_path),
            str(remote_path),
        ))
        == set()
    ), "no branch name or path is recorded"


def test_base_that_is_not_behind_records_no_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_recorder: RecordingRecorder,
) -> None:
    """An opted-in pull for a current base records that there is nothing to do."""
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", _fail_prompt)

    exit_code = donkey.run_git_donkey(
        "feature/current-base",
        "main",
        options=donkey._PullOptions(pull_rebase=True),
    )

    assert exit_code == 0, "a current base still creates the worktree"
    assert recording_recorder.outcomes("base_update") == ["not_behind"], (
        "a current base records that it needs no update"
    )
    assert recording_recorder.pull_modes("base_update") == ["rebase"], (
        "the requested mode is recorded"
    )
    assert recording_recorder.base_kinds("base_update") == ["explicit"], (
        "the record names the explicit base"
    )


def test_declined_prompt_records_the_declined_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_recorder: RecordingRecorder,
) -> None:
    """Declining the prompt records the declined update and pulls nothing."""
    repo = _behind_repo(tmp_path)
    monkeypatch.chdir(repo.working_tree_dir or ".")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", lambda *_: False)

    exit_code = donkey.run_git_donkey(
        "feature/declined", ".", options=donkey._PullOptions(pull_ff=True)
    )

    assert exit_code == 0, "declining still creates the worktree"
    assert recording_recorder.outcomes("base_update") == ["declined"], (
        "the declined prompt is recorded"
    )
    assert recording_recorder.pull_modes("base_update") == ["ff_only"], (
        "the requested mode is recorded"
    )
    assert "pull_execution" not in recording_recorder.span_operations(), (
        "a declined update pulls nothing"
    )


def test_base_update_records_its_start_and_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_recorder: RecordingRecorder,
) -> None:
    """An accepted fast-forward pull records the update start and result."""
    repo = _behind_repo(tmp_path)
    monkeypatch.chdir(repo.working_tree_dir or ".")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", lambda *_: True)

    exit_code = donkey.run_git_donkey(
        "feature/ff-observed", ".", options=donkey._PullOptions(pull_ff=True)
    )

    assert exit_code == 0, "the updated base still creates the worktree"
    assert recording_recorder.outcomes("base_update") == ["started", "success"], (
        "the update reports its start and its result"
    )
    assert recording_recorder.pull_modes("base_update") == ["ff_only", "ff_only"], (
        "both records name the fast-forward-only mode"
    )
    assert "pull_execution" in recording_recorder.span_operations(), (
        "the pull itself is timed"
    )
    assert recording_recorder.unbounded_values() == set(), (
        "every recorded value is drawn from a fixed vocabulary"
    )


def test_failed_base_update_records_the_git_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_recorder: RecordingRecorder,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A divergent base records the failed update, not the Git output."""
    repo = _behind_repo(tmp_path)
    _seed_repo(repo, "local.txt", "local change")
    monkeypatch.chdir(repo.working_tree_dir or ".")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", lambda *_: True)

    with pytest.raises(SystemExit) as excinfo:
        donkey.run_git_donkey(
            "feature/failed", ".", options=donkey._PullOptions(pull_ff=True)
        )

    assert excinfo.value.code == 1, "divergence under --ff-only is still an error"
    assert capsys.readouterr().err, "the failure is still reported to the user"
    assert recording_recorder.outcomes("base_update") == ["started", "failure"], (
        "the update reports its start and its failure"
    )
    assert recording_recorder.error_kinds("base_update") == ["git_command_error"], (
        "the failure class is the Git command"
    )
    assert (
        recording_recorder.leaked_details(("fast-forward", "fatal", "local.txt"))
        == set()
    ), "the Git output is not recorded"


def test_base_outside_a_worktree_records_the_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_recorder: RecordingRecorder,
) -> None:
    """A base held by no worktree records that failure class."""
    repo = _behind_repo(tmp_path)
    repo.git.checkout("-b", "unrelated")
    _seed_repo(repo, "unrelated.txt", "unrelated change")
    monkeypatch.chdir(repo.working_tree_dir or ".")
    monkeypatch.setattr(donkey.helpers, "_prompt_yes_no", lambda *_: True)

    with pytest.raises(SystemExit) as excinfo:
        donkey.run_git_donkey(
            "feature/unowned", "main", options=donkey._PullOptions(pull_rebase=True)
        )

    assert excinfo.value.code == 1, "an unowned base is still an error"
    assert recording_recorder.outcomes("base_update") == ["failure"], (
        "the unowned base is recorded as a failure"
    )
    assert recording_recorder.error_kinds("base_update") == ["base_not_in_worktree"], (
        "the failure class names the missing worktree"
    )
    assert "pull_execution" not in recording_recorder.span_operations(), (
        "no pull is attempted without a worktree"
    )


def test_creation_conflict_records_its_start_and_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_recorder: RecordingRecorder,
) -> None:
    """A refused creation records its start and the failure."""
    local_path, _remote_path = _setup_repo(tmp_path)
    monkeypatch.chdir(local_path)

    with pytest.raises(SystemExit) as excinfo:
        donkey.run_git_donkey("main")

    assert excinfo.value.code == 1, "an existing checkout is still a conflict"
    assert recording_recorder.outcomes("worktree_creation") == ["started", "failure"], (
        "creation reports its start and the conflict"
    )
    assert recording_recorder.error_kinds("worktree_creation") == [
        "worktree_creation_error"
    ], "the failure class names the refused creation"
    assert recording_recorder.outcomes("template_overlay") == [], (
        "the failed run never reaches the overlay"
    )


def test_unselectable_template_records_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_recorder: RecordingRecorder,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A repository whose template cannot be selected records it as unavailable."""
    local_path, remote_path = _setup_repo(tmp_path)
    repo = Repo(local_path)
    repo.create_remote("upstream", remote_path.as_posix())
    repo.create_remote("mirror", remote_path.as_posix())
    repo.delete_remote(repo.remote("origin"))
    monkeypatch.chdir(local_path)

    exit_code = donkey.run_git_donkey("feature/ambiguous-template", "main")

    assert exit_code == 0, "an unusable template selection is not a failure"
    assert "Multiple remotes" in capsys.readouterr().err, (
        "the selection problem is still reported to the user"
    )
    assert recording_recorder.outcomes("template_overlay") == ["unavailable"], (
        "the unusable overlay reports itself unavailable"
    )
    assert recording_recorder.error_kinds("template_overlay") == ["selection_error"], (
        "the failure class names the template selection"
    )
    assert recording_recorder.unbounded_values() == set(), (
        "every recorded value is drawn from a fixed vocabulary"
    )


def test_applied_template_records_its_start_and_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_recorder: RecordingRecorder,
) -> None:
    """An applicable overlay records its start and its result."""
    local_path, remote_path = _setup_repo(tmp_path)
    template_dir = _isolate_template_base(
        tmp_path, monkeypatch
    ) / slugs.slug_dash_adler32(remote_path.as_posix())
    template_dir.mkdir(parents=True)
    (template_dir / ".editorconfig").write_text("[*]\nindent_size = 2\n")
    monkeypatch.chdir(local_path)

    exit_code = donkey.run_git_donkey("feature/overlaid", "main")

    assert exit_code == 0, "an applied overlay keeps the run successful"
    assert recording_recorder.outcomes("template_overlay") == ["started", "success"], (
        "the overlay reports its start and its result"
    )
    assert recording_recorder.unbounded_values() == set(), (
        "every recorded value is drawn from a fixed vocabulary"
    )


def test_failed_template_records_the_os_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_recorder: RecordingRecorder,
) -> None:
    """A failed overlay records the error class, not its message."""
    local_path, remote_path = _setup_repo(tmp_path)
    template_dir = _isolate_template_base(
        tmp_path, monkeypatch
    ) / slugs.slug_dash_adler32(remote_path.as_posix())
    template_dir.mkdir(parents=True)
    monkeypatch.setattr(templates, "apply_template", _fail_apply)
    monkeypatch.chdir(local_path)

    exit_code = donkey.run_git_donkey("feature/overlay-failed", "main")

    assert exit_code == 1, "a failed overlay is still reported as a failure"
    assert recording_recorder.outcomes("template_overlay") == ["started", "failure"], (
        "the overlay reports its start and its failure"
    )
    assert recording_recorder.error_kinds("template_overlay") == ["os_error"], (
        "the failure class names the operating system error"
    )
    assert (
        recording_recorder.leaked_details((
            "simulated overlay failure",
            str(local_path),
        ))
        == set()
    ), "the exception message and the local paths are not recorded"
