"""Exercise the actual Cyclopts parser for git-plonk's cleanup modes."""

from __future__ import annotations

import typing as typ

import pytest

from git_donkey import cli, plonk

# Exit status ``helpers._die`` uses when the command rejects its own arguments,
# such as ``--soft`` with ``--hard``: the flags parse, but the combination is
# unusable. Cyclopts itself exits 1 for a parse error.
_USAGE_ERROR_EXIT_CODE = 2


def test_plonk_cli_rejects_soft_and_hard_together() -> None:
    """The CLI boundary should reject mutually exclusive cleanup modes."""
    with pytest.raises(SystemExit) as exc_info:
        cli._plonk_app(["--soft", "--hard"])

    assert exc_info.value.code == _USAGE_ERROR_EXIT_CODE, (
        "expected conflicting flags to be usage error"
    )


@pytest.mark.parametrize(
    "flag",
    [
        pytest.param("--no-github", id="no-github"),
        pytest.param("--upstream-gone", id="upstream-gone"),
    ],
)
def test_plonk_cli_rejects_completion_tiers_with_soft(flag: str) -> None:
    """Completion tiers mean nothing to soft cleanup and must be refused."""
    with pytest.raises(SystemExit) as exc_info:
        cli._plonk_app(["--soft", flag])

    assert exc_info.value.code == _USAGE_ERROR_EXIT_CODE, (
        f"expected --soft with {flag} to be a usage error"
    )


def test_plonk_cli_passes_dry_run_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI boundary should pass dry-run intent to the workflow runner."""
    captured_options: dict[str, object] = {}

    def run_plonk_stub(
        *,
        soft: bool = False,
        hard: bool = False,
        dry_run: bool = False,
        options: plonk._PlonkRunOptions = plonk._DEFAULT_RUN_OPTIONS,
    ) -> int:
        """Capture the options the CLI passes and report success."""
        captured_options.update(
            soft=soft,
            hard=hard,
            dry_run=dry_run,
            options=options,
        )
        return 0

    monkeypatch.setattr(plonk, "run_git_plonk", run_plonk_stub)

    with pytest.raises(SystemExit) as exc_info:
        cli._plonk_app(["--hard", "--dry-run"])

    assert exc_info.value.code == 0, "expected dry-run CLI invocation to succeed"
    assert captured_options["soft"] is False, "expected CLI to leave soft mode disabled"
    assert captured_options["hard"] is True, "expected CLI to select hard mode"
    assert captured_options["dry_run"] is True, "expected CLI to pass the dry-run flag"
    assert captured_options["options"] == plonk._DEFAULT_RUN_OPTIONS, (
        "expected CLI to pass the default completion-tier options"
    )


def test_plonk_cli_passes_completion_tier_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI should spread the completion-tier options to the runner."""
    captured_options: dict[str, object] = {}

    def run_plonk_stub(
        *,
        soft: bool = False,
        hard: bool = False,
        dry_run: bool = False,
        options: plonk._PlonkRunOptions = plonk._DEFAULT_RUN_OPTIONS,
    ) -> int:
        """Capture the options the CLI passes and report success."""
        captured_options.update(
            soft=soft,
            hard=hard,
            dry_run=dry_run,
            options=options,
        )
        return 0

    monkeypatch.setattr(plonk, "run_git_plonk", run_plonk_stub)

    with pytest.raises(SystemExit) as exc_info:
        cli._plonk_app(["--hard", "--no-github", "--upstream-gone"])

    assert exc_info.value.code == 0, "expected the tier flags to parse"
    captured = typ.cast("plonk._PlonkRunOptions", captured_options["options"])
    assert captured == plonk._PlonkRunOptions(
        no_github=True,
        upstream_gone=True,
    ), "expected both completion-tier flags to reach the runner"
