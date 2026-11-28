"""Unit tests for the read-only GitHub completion fallback.

The fallback is the one tier that leaves the machine, so its contract is
narrow: parse only the GitHub remote URL forms Git itself supports, ask ``gh``
one read-only question with prompts disabled and stdin closed, and accept an
answer only when it names the requested head branch, base branch, and head
owner. Every other outcome must degrade to ``unknown`` so the sweep can leave
the candidate unverified rather than remove it on a guess.
"""

from __future__ import annotations

import json
import subprocess  # ruff: ignore[suspicious-subprocess-import]  # asserts the boundary
import typing as typ

import pytest

from git_donkey import plonk_github

if typ.TYPE_CHECKING:
    from tests.observability_helpers import RecordingRecorder

_HEAD_BRANCH = "fix-markdown-gates"
_BASE_BRANCH = "main"
_OWNER = "leynos"
_REPOSITORY = "git-donkey"
_HEAD_SHA = "a" * 40
_QUERY = plonk_github._PullRequestQuery(
    owner=_OWNER,
    repository=_REPOSITORY,
    head_branch=_HEAD_BRANCH,
    base_branch=_BASE_BRANCH,
    head_sha=_HEAD_SHA,
)
_EXPECTED_ARGUMENTS = [
    "gh",
    "pr",
    "list",
    "--repo",
    f"{_OWNER}/{_REPOSITORY}",
    "--head",
    _HEAD_BRANCH,
    "--base",
    _BASE_BRANCH,
    "--state",
    "merged",
    "--json",
    "number,headRefName,baseRefName,headRepositoryOwner,headRefOid",
    "--limit",
    str(plonk_github._GH_RESULT_LIMIT),
]


@pytest.mark.parametrize(
    ("url", "owner", "name"),
    [
        pytest.param(
            "git@github.com:leynos/git-donkey.git",
            "leynos",
            "git-donkey",
            id="scp-like-with-suffix",
        ),
        pytest.param(
            "git@github.com:leynos/git-donkey",
            "leynos",
            "git-donkey",
            id="scp-like-without-suffix",
        ),
        pytest.param(
            "ssh://git@github.com/leynos/git-donkey.git",
            "leynos",
            "git-donkey",
            id="ssh-url",
        ),
        pytest.param(
            "ssh://git@github.com:22/leynos/git-donkey.git",
            "leynos",
            "git-donkey",
            id="ssh-url-with-port",
        ),
        pytest.param(
            "ssh://git@ssh.github.com:443/leynos/git-donkey.git",
            "leynos",
            "git-donkey",
            id="ssh-url-port-443-host",
        ),
        pytest.param(
            "ssh+git://git@github.com/leynos/git-donkey.git",
            "leynos",
            "git-donkey",
            id="ssh-plus-git-scheme",
        ),
        pytest.param(
            "git+ssh://git@github.com/leynos/git-donkey.git",
            "leynos",
            "git-donkey",
            id="git-plus-ssh-scheme",
        ),
        pytest.param(
            "git://github.com/leynos/git-donkey.git",
            "leynos",
            "git-donkey",
            id="git-protocol",
        ),
        pytest.param(
            "https://github.com/leynos/git-donkey.git",
            "leynos",
            "git-donkey",
            id="https-with-suffix",
        ),
        pytest.param(
            "https://github.com/leynos/git-donkey",
            "leynos",
            "git-donkey",
            id="https-without-suffix",
        ),
        pytest.param(
            "https://github.com:443/leynos/git-donkey.git",
            "leynos",
            "git-donkey",
            id="https-explicit-default-port",
        ),
        pytest.param(
            "https://GitHub.com/leynos/git-donkey.git",
            "leynos",
            "git-donkey",
            id="mixed-case-host",
        ),
        pytest.param(
            "https://token@github.com/leynos/git-donkey.git",
            "leynos",
            "git-donkey",
            id="https-with-credentials",
        ),
        pytest.param(
            "  git@github.com:leynos/git-donkey.git\n",
            "leynos",
            "git-donkey",
            id="surrounding-whitespace",
        ),
    ],
)
def test_supported_github_remote_urls_are_parsed(
    url: str,
    owner: str,
    name: str,
) -> None:
    """Every GitHub URL form Git supports should name its repository."""
    assert plonk_github.parse_github_remote_url(url) == (
        plonk_github._GitHubRepository(owner, name)
    ), "expected the configured GitHub remote to be recognized"


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("", id="empty"),
        pytest.param("/home/user/repos/demo.git", id="local-path"),
        pytest.param(
            "https://gitlab.com/leynos/git-donkey.git",
            id="other-host",
        ),
        pytest.param("git@github.com:leynos.git", id="no-repository"),
        pytest.param("git@github.com:/git-donkey.git", id="no-owner"),
        pytest.param("git@github.com:leynos/git-donkey/extra.git", id="extra-path"),
        pytest.param("ssh://git@example.com/leynos/git-donkey.git", id="ssh-other"),
        pytest.param("github.com/leynos/git-donkey", id="no-scheme"),
        pytest.param("git@github.com:-leynos/git-donkey.git", id="option-like-owner"),
        pytest.param("git@github.com:leynos/git donkey.git", id="space-in-name"),
        pytest.param("git@github.com:leynos/git;donkey.git", id="punctuation-in-name"),
        pytest.param(
            "https://github.com:8443/leynos/git-donkey.git",
            id="non-default-https-port",
        ),
    ],
)
def test_non_github_remote_urls_are_rejected(url: str) -> None:
    """A URL the parser cannot vouch for should disable the fallback."""
    assert plonk_github.parse_github_remote_url(url) is None, (
        "expected a non-GitHub or malformed URL to be rejected"
    )


def _record(**overrides: object) -> dict[str, object]:
    """Return a matching ``gh pr list --json`` record with overrides."""
    record: dict[str, object] = {
        "number": 61,
        "headRefName": _HEAD_BRANCH,
        "baseRefName": _BASE_BRANCH,
        "headRepositoryOwner": {"login": _OWNER},
        "headRefOid": _HEAD_SHA,
    }
    record.update(overrides)
    return record


def _lookup(
    payload: object,
    *,
    returncode: int = 0,
    query: plonk_github._PullRequestQuery = _QUERY,
) -> plonk_github._MergedPullRequest:
    """Run the lookup against a fake ``gh`` that prints ``payload``."""
    stdout = payload if isinstance(payload, str) else json.dumps(payload)

    def run(arguments: typ.Sequence[str]) -> subprocess.CompletedProcess[str]:
        """Return the configured process result and record the arguments."""
        assert list(arguments) == _EXPECTED_ARGUMENTS, (
            "expected the lookup to ask gh for one bounded merged-PR query"
        )
        return subprocess.CompletedProcess(arguments, returncode, stdout, "")

    return plonk_github.merged_pull_request(query, run=run)


def test_matching_merged_pull_request_is_accepted() -> None:
    """A response naming the queried head, base, and owner is authoritative."""
    result = _lookup([_record()])

    assert result == plonk_github._MergedPullRequest("merged", 61), (
        "expected a fully matching merged pull request to complete the branch"
    )


def test_owner_comparison_is_case_insensitive() -> None:
    """GitHub logins are case-insensitive, so the owner check should be too."""
    result = _lookup([_record(headRepositoryOwner={"login": "Leynos"})])

    assert result.state == "merged", (
        "expected the canonical owner casing to match the configured URL"
    )


def test_head_commit_comparison_is_case_insensitive() -> None:
    """A hex commit identifier is case-insensitive."""
    result = _lookup([_record(headRefOid=_HEAD_SHA.upper())])

    assert result.state == "merged", (
        "expected the same commit in different hex casing to match"
    )


def test_reused_branch_name_with_new_work_is_not_merged() -> None:
    """An old merge must not complete a branch name reused for new work."""
    result = _lookup([_record(headRefOid="2" * 40)])

    assert result.state == "unknown", (
        "expected a merge whose head commit differs from the local tip to fail "
        "closed rather than complete new work"
    )


def test_matching_record_after_a_fork_is_accepted() -> None:
    """A fork row ahead of the configured owner's row must not hide it."""
    fork_record = _record(headRepositoryOwner={"login": "fork"})

    result = _lookup([fork_record, _record()])

    assert result == plonk_github._MergedPullRequest("merged", 61), (
        "expected the local identity filter to consider the whole page"
    )


def test_lookup_records_a_bounded_span_and_failure(
    recording_recorder: RecordingRecorder,
) -> None:
    """A failed response should leave a bounded failure observation."""
    result = _lookup([{"unexpected": "record"}])

    assert result.state == "unknown", "expected a malformed record to be unknown"
    assert recording_recorder.span_operations() == ["github_lookup"], (
        "expected the gh process boundary to be timed"
    )
    assert [
        (observation.operation, observation.outcome)
        for observation in recording_recorder.observations
    ] == [("github_lookup", "failure")], (
        "expected the failed lookup to record one bounded failure"
    )
    assert recording_recorder.unbounded_values() == set(), (
        "every recorded value comes from a declared vocabulary"
    )


def test_lookup_records_a_bounded_span_and_success(
    recording_recorder: RecordingRecorder,
) -> None:
    """A successful query should record a success observation."""
    result = _lookup([_record()])

    assert result.state == "merged", "expected the matching record to complete"
    assert recording_recorder.span_operations() == ["github_lookup"], (
        "expected the gh process boundary to be timed"
    )
    assert [
        (observation.operation, observation.outcome)
        for observation in recording_recorder.observations
    ] == [("github_lookup", "success")], (
        "expected the successful lookup to record one bounded success"
    )


def test_empty_result_is_not_merged() -> None:
    """No merged pull request is a definitive answer, not an unknown."""
    assert _lookup([]) == plonk_github._MergedPullRequest("not_merged"), (
        "expected an empty gh result to report no merged pull request"
    )


@pytest.mark.parametrize(
    "record",
    [
        pytest.param(_record(headRefName="another-branch"), id="different-head"),
        pytest.param(_record(baseRefName="release"), id="different-base"),
        pytest.param(_record(headRepositoryOwner={"login": "other"}), id="other-owner"),
        pytest.param(_record(headRepositoryOwner=None), id="missing-owner"),
        pytest.param(_record(headRefOid="2" * 40), id="different-head-commit"),
        pytest.param(_record(headRefOid=None), id="missing-head-commit"),
        pytest.param(_record(number="61"), id="string-number"),
        pytest.param(_record(number=True), id="boolean-number"),
        pytest.param(_record(number=None), id="missing-number"),
        pytest.param(_record(number=0), id="zero-number"),
        pytest.param(_record(number=-1), id="negative-number"),
        pytest.param("not-a-record", id="non-object-record"),
    ],
)
def test_mismatched_or_malformed_records_fail_closed(record: object) -> None:
    """A response that does not match the query must never count as merged."""
    result = _lookup([record])

    assert result.state == "unknown", (
        "expected a mismatched or malformed record to be unknown, not merged"
    )


def test_non_list_payload_fails_closed() -> None:
    """The JSON shape is part of the contract; anything else is unknown."""
    assert _lookup({"number": 61}).state == "unknown", (
        "expected a JSON object where an array is required to be unknown"
    )


def test_invalid_json_fails_closed() -> None:
    """Malformed output from gh must not be interpreted as an answer."""
    assert _lookup("{not json").state == "unknown", (
        "expected malformed JSON to map to unknown"
    )


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("9" * 5000, id="integer-conversion-limit"),
        pytest.param("[" * 2000 + "]" * 2000, id="deeply-nested"),
    ],
)
def test_decoder_errors_fail_closed(payload: str) -> None:
    """Decoder limits are lookup failures, not sweep failures."""
    assert _lookup(payload).state == "unknown", (
        "expected a decoder failure to map to unknown"
    )


def test_non_zero_exit_fails_closed() -> None:
    """A failed gh invocation must not count as a merged pull request."""
    result = _lookup([_record()], returncode=1)

    assert result.state == "unknown", "expected a failed gh query to be unknown"


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(subprocess.TimeoutExpired(["gh"], 10), id="timeout"),
        pytest.param(OSError("gh disappeared"), id="os-error"),
        pytest.param(
            UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
            id="decode-error",
        ),
    ],
)
def test_process_errors_fail_closed(error: BaseException) -> None:
    """A timeout, execution error, or decoding failure must be unknown."""

    def run(arguments: typ.Sequence[str]) -> subprocess.CompletedProcess[str]:
        """Raise the configured process error."""
        raise error

    result = plonk_github.merged_pull_request(_QUERY, run=run)

    assert result.state == "unknown", (
        f"expected {type(error).__name__} to map to unknown"
    )


def test_gh_available_reflects_path_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Availability should be exactly whether an executable can be found."""
    monkeypatch.setattr(plonk_github.shutil, "which", lambda name: None)
    assert not plonk_github.gh_available(), "expected a missing gh to be unavailable"

    monkeypatch.setattr(plonk_github.shutil, "which", lambda name: "/bin/gh")
    assert plonk_github.gh_available(), "expected a resolvable gh to be available"


def test_run_gh_disables_prompts_and_closes_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The process boundary should be non-interactive and bounded by a timeout."""
    captured: dict[str, object] = {}

    def fake_which(name: str) -> str:
        """Pretend gh resolves to a fixed absolute path."""
        assert name == "gh", "expected the runner to resolve gh itself"
        return "/usr/bin/gh"

    def fake_run(
        arguments: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        """Capture the arguments and keyword arguments of the invocation."""
        captured["arguments"] = arguments
        captured.update(kwargs)
        return subprocess.CompletedProcess(arguments, 0, "[]", "")

    monkeypatch.setattr(plonk_github.shutil, "which", fake_which)
    monkeypatch.setattr(plonk_github.subprocess, "run", fake_run)

    completed = plonk_github._run_gh(["gh", "pr", "list"])

    assert completed.returncode == 0, "expected the fake invocation to succeed"
    assert captured["arguments"] == ["/usr/bin/gh", "pr", "list"], (
        "expected gh to be resolved to its absolute path before execution"
    )
    assert captured["stdin"] is subprocess.DEVNULL, "expected stdin to be closed"
    assert captured["timeout"] == plonk_github._DEFAULT_TIMEOUT_SECONDS, (
        "expected the invocation to be bounded by the module timeout"
    )
    environment = typ.cast("dict[str, str]", captured["env"])
    assert environment[plonk_github._GH_PROMPT_DISABLED_ENV] == "1", (
        "expected prompts to be disabled for a non-interactive sweep"
    )
    assert environment[plonk_github._GH_HOST_ENV] == "github.com", (
        "expected the lookup to be pinned to the parsed GitHub host"
    )
    assert captured["check"] is False, "expected the exit status to be inspected"
    assert captured["capture_output"] is True, "expected both streams to be captured"
    assert captured["text"] is True, "expected text rather than bytes"


def test_run_gh_does_not_invoke_a_missing_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing gh should become a non-zero result without a subprocess."""
    monkeypatch.setattr(plonk_github.shutil, "which", lambda name: None)
    called = False

    def fake_run(*args: object, **kwargs: object) -> None:
        """Fail if the runner tries to execute a tool it could not resolve."""
        nonlocal called
        called = True

    monkeypatch.setattr(plonk_github.subprocess, "run", fake_run)

    completed = plonk_github._run_gh(["gh"])

    assert completed.returncode == plonk_github._GH_MISSING_STATUS, (
        "expected a missing tool to report failure"
    )
    assert not called, "expected no subprocess when gh cannot be resolved"
