"""Unit contracts for remote default discovery and opt-in pull selection."""

from __future__ import annotations

import typing as typ

import pytest

from git_donkey import donkey

if typ.TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("advertisement", "expected"),
    [
        ("ref: refs/heads/trunk\tHEAD\nabc\tHEAD", "trunk"),
        ("ref: refs/heads/release/stable\tHEAD", "release/stable"),
        ("abc\tHEAD", None),
        ("", None),
        ("ref: refs/tags/v1\tHEAD", None),
        ("ref: refs/heads/main\tother", None),
        ("garbage refs/heads/main HEAD", None),
        ("ref: refs/heads/main HEAD extra", None),
    ],
)
def test_advertised_default_branch(
    advertisement: str,
    expected: str | None,
) -> None:
    """Only the advertised HEAD branch supplies an implicit base."""
    assert donkey._advertised_default_branch(advertisement) == expected


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        (donkey._PullOptions(), None),
        (donkey._PullOptions(pull_rebase=True), "--rebase"),
        (donkey._PullOptions(pull_ff=True), "--ff-only"),
    ],
)
def test_pull_mode_is_explicit(
    options: donkey._PullOptions,
    expected: str | None,
) -> None:
    """Pulling has no default mode and each opt-in selects one strategy."""
    assert donkey._pull_mode(options, no_pull=False) == expected


@pytest.mark.parametrize(
    "pull_case",
    [
        (donkey._PullOptions(pull_rebase=True, pull_ff=True), False),
        (donkey._PullOptions(pull_rebase=True), True),
        (donkey._PullOptions(pull_ff=True), True),
    ],
)
def test_conflicting_pull_options_fail_before_repository_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    pull_case: tuple[donkey._PullOptions, bool],
) -> None:
    """Conflicting flags are usage errors, even outside a repository."""
    options, no_pull = pull_case
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        donkey.run_git_donkey("feature/test", options=options, no_pull=no_pull)
    assert excinfo.value.code == 2
    assert "mutually exclusive" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_no_pull_remains_a_compatible_no_op() -> None:
    """The existing no-pull option retains the new non-pulling default."""
    assert donkey._pull_mode(donkey._PullOptions(), no_pull=True) is None
