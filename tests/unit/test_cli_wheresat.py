"""Exercise the argument boundary git-wheresat reports its failures through.

Cyclopts reports a parse failure as a panel on standard error and exits ``1``,
and ``1`` is this command's status for a boundary the evidence refused. The
console entrypoint turns that failure into the error envelope when the caller
asked for one, so these tests pin both the interception and the ordinary paths
it must leave alone.
"""

from __future__ import annotations

import json

import pytest

from git_donkey import cli, wheresat, wheresat_records, wheresat_report

# What Cyclopts exits with when it reports a parse failure itself.
_CYCLOPTS_EXIT_CODE = 1

# An argument the parser cannot coerce, which is the failure a caller reading a
# stream of envelopes meets most often.
_COERCION = ("--limit", "notanumber")

# An option the parser does not know at all.
_UNKNOWN_OPTION = "--nope"

# The window a successful parse is asked for, and the value it must hand over.
_LIMIT = 3


def test_an_argument_the_parser_cannot_coerce_is_an_envelope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A run that asked for --json is given the envelope however it fails."""
    with pytest.raises(SystemExit) as exc_info:
        cli._wheresat_main(["--json", *_COERCION])

    assert exc_info.value.code == wheresat_records.EXIT_USAGE, (
        "a refused argument is a usage failure, not a refused boundary"
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["schema"] == wheresat_report.JSON_SCHEMA, (
        "the envelope is the versioned one a reader is told to expect"
    )
    assert payload["exitCode"] == wheresat_records.EXIT_USAGE, (
        "the status the process exits with is the one the envelope carries"
    )
    assert "notanumber" in payload["error"], (
        "the parser's own message names the value it refused"
    )
    assert not captured.err, "the envelope is the only document written"


def test_an_unknown_option_asked_for_as_json_is_an_envelope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The flag is found wherever it stands, not only before the failure."""
    with pytest.raises(SystemExit) as exc_info:
        cli._wheresat_main([_UNKNOWN_OPTION, "--json"])

    assert exc_info.value.code == wheresat_records.EXIT_USAGE, (
        "an option the parser refuses is a usage failure too"
    )
    payload = json.loads(capsys.readouterr().out)
    assert _UNKNOWN_OPTION in payload["error"], (
        "the parser's message names the option it did not know"
    )


def test_a_parse_failure_without_json_keeps_the_parser_diagnostic(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A run that did not ask for the envelope keeps its prose and status."""
    with pytest.raises(SystemExit) as exc_info:
        cli._wheresat_main(list(_COERCION))

    assert exc_info.value.code == _CYCLOPTS_EXIT_CODE, (
        "a run that did not ask for the envelope exits as it always did"
    )
    captured = capsys.readouterr()
    assert not captured.out, "no envelope is written unasked"
    assert "notanumber" in captured.err, "the parser's panel is still printed"


@pytest.mark.parametrize("asking", ["--json=false", "--json=0", "--no-json"])
def test_json_turned_off_is_not_a_request_for_the_envelope(
    asking: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A flag that turns the envelope off is read as turning it off."""
    with pytest.raises(SystemExit) as exc_info:
        cli._wheresat_main([asking, *_COERCION])

    assert exc_info.value.code == _CYCLOPTS_EXIT_CODE, (
        f"{asking} leaves the parser's own status alone"
    )
    assert not capsys.readouterr().out, f"{asking} did not ask for the envelope"


def test_a_run_that_parses_is_left_to_the_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A parse that succeeds reaches the command, envelope asked for or not."""
    seen: list[wheresat.WheresatOptions] = []

    def run_wheresat_stub(options: wheresat.WheresatOptions) -> int:
        seen.append(options)
        return 0

    monkeypatch.setattr(wheresat, "run_git_wheresat", run_wheresat_stub)

    with pytest.raises(SystemExit) as exc_info:
        cli._wheresat_main(["--json", "--limit", str(_LIMIT)])

    assert exc_info.value.code == 0, "the run's own status is untouched"
    assert seen[0].limit == _LIMIT, "the command is handed what the parser read"
    assert seen[0].json is True, "and the envelope flag with it"
