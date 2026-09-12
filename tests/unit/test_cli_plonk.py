"""Exercise the actual Cyclopts parser for git-plonk's cleanup modes."""

from __future__ import annotations

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
