"""Contract tests for scanning every executable ``run`` script.

The structural tests read the parsed workflows; this module proves the scan
reaches every declared step, ignores a value merely named ``run`` inside an
``env`` mapping, and rejects pasted template expressions outside the one
reviewed exception.
"""

from __future__ import annotations

import typing as typ

import pytest

from tests.release_contract import ci_documents, expansions_in, run_scripts

#: The one reviewed template expansion inside a shell script. It renders only
#: `true` or `false`, and the shared CV-005 contract (`token.check-step`)
#: requires the secret to be read there rather than through an `env` value.
_CREDENTIAL_CHECK_COMMAND: typ.Final = (
    'echo "available=${{ secrets.CS_ACCESS_TOKEN != \'\' }}" >> "$GITHUB_OUTPUT"'
)
_ALLOWED_EXPANSIONS: typ.Final = frozenset({
    ("coverage-main.yml", "codescene-token", _CREDENTIAL_CHECK_COMMAND)
})
#: A floor, not the exact count, so adding a step does not require an edit.
_MIN_SCANNED_SCRIPTS = 20


def test_no_run_script_expands_a_template_expression() -> None:
    """Values reach a shell through ``env``, never by pasting into the script."""
    documents = ci_documents()
    scanned = sum(
        len(list(run_scripts(parsed, name))) for name, parsed in documents.items()
    )
    assert scanned > _MIN_SCANNED_SCRIPTS, (
        "the scan must reach the repository's run scripts"
    )
    assert ".github/actions/pure-python-wheel/action.yml" in documents, (
        "the scan must reach the distribution build action"
    )
    findings = [
        where
        for name, parsed in documents.items()
        for where in expansions_in(name, parsed, _ALLOWED_EXPANSIONS)
    ]
    assert not findings, f"route these values through env: {findings}"


@pytest.mark.parametrize(
    ("name", "parsed", "is_flagged"),
    [
        (
            "x.yml",
            {"jobs": {"test": {"steps": [{"run": 'echo "${{ inputs.target }}"'}]}}},
            True,
        ),
        (
            "x.yml",
            {"runs": {"steps": [{"run": "make ${{ matrix.check }}"}]}},
            True,
        ),
        (
            "x.yml",
            {
                "jobs": {
                    "test": {
                        "steps": [{"env": {"T": "${{ inputs.t }}"}, "run": 'echo "$T"'}]
                    }
                }
            },
            False,
        ),
        (
            "x.yml",
            {
                "jobs": {
                    "test": {
                        "steps": [
                            {
                                "id": "codescene-token",
                                "run": _CREDENTIAL_CHECK_COMMAND,
                            }
                        ]
                    }
                }
            },
            True,
        ),
        (
            "coverage-main.yml",
            {
                "jobs": {
                    "test": {
                        "steps": [
                            {
                                "id": "codescene-token",
                                "run": _CREDENTIAL_CHECK_COMMAND,
                            }
                        ]
                    }
                }
            },
            False,
        ),
    ],
    ids=[
        "input",
        "composite-matrix",
        "through-env",
        "exception-elsewhere",
        "exception",
    ],
)
def test_the_expansion_scan_discriminates(
    name: str, parsed: object, *, is_flagged: bool
) -> None:
    """The scan flags pasted values and admits only the one reviewed exception."""
    assert bool(expansions_in(name, parsed, _ALLOWED_EXPANSIONS)) is is_flagged, (
        "the expansion scan must flag pasted values and admit the exception"
    )


def test_the_run_script_scan_ignores_environment_bindings() -> None:
    """An env value named run is not an executable script."""
    parsed = {
        "jobs": {
            "test": {
                "steps": [
                    {"env": {"run": "${{ inputs.bad }}"}, "run": "echo safe"},
                ]
            }
        }
    }

    assert not expansions_in("x.yml", parsed, _ALLOWED_EXPANSIONS), (
        "an env binding named run must not be scanned as a script"
    )
    assert len(list(run_scripts(parsed, "doc"))) == 1, (
        "only the executable step run script must be scanned"
    )


def test_the_run_script_scan_reaches_every_declared_step() -> None:
    """Both workflow jobs and composite-action steps are scanned."""
    parsed = {
        "jobs": {
            "first": {"steps": [{"run": "echo one"}, {"uses": "x@y"}]},
            "second": {"steps": [{"run": "echo two"}]},
        },
        "runs": {"steps": [{"run": "echo three"}]},
    }

    scripts = [holder["run"] for _, holder in run_scripts(parsed, "doc")]

    assert scripts == ["echo one", "echo two", "echo three"], (
        "every declared step with a run script must be scanned once"
    )
