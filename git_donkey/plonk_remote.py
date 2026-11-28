"""Remote-advertisement probing for git-plonk's opt-in upstream check.

The upstream check asks a remote whether a configured branch ref still exists.
That question crosses the network, which is a different boundary from the local
Git reads :mod:`git_donkey.plonk_adapters` performs, so it lives in its own unit
with an explicit name. :class:`_RemoteRefProbe` owns the bounded
``git ls-remote`` call and the observation it leaves; the adapter composes it
and asks the probe only after validating the configured ref.

Every failure is reported as ``unknown``: a missing tool, a non-zero status
that is not "no matching ref", a timeout, or output that does not name the
exact ref. Only a successful advertisement that omits an exact, valid ref is
``gone``, and the caller still treats that as opt-in evidence only.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]  # bounded query
import typing as typ

from git_donkey import observability

if typ.TYPE_CHECKING:
    from git_donkey.plonk_records import UpstreamState

_LS_REMOTE_NO_MATCH_STATUS = 2
# A query that answered the advertisement, whether or not a ref matched.
_LS_REMOTE_QUERY_STATUSES = frozenset({0, _LS_REMOTE_NO_MATCH_STATUS})
_LS_REMOTE_TIMEOUT_SECONDS = 10.0
_GIT_TERMINAL_PROMPT_ENV = "GIT_TERMINAL_PROMPT"
# Characters Git forbids in ref names, plus the ones `ls-remote` treats as a
# pattern. A merge ref containing any of them cannot be queried literally.
_INVALID_REF_CHARS = frozenset(" ~^:?*[\\")
# Multi-character sequences Git also forbids in a ref name.
_FORBIDDEN_REF_SEQUENCES = ("..", "//", "@{")
_FIRST_PRINTABLE_CODE_POINT = 0x20
_DELETE_CODE_POINT = 0x7F


def is_exact_branch_ref(ref: str) -> bool:
    """Return whether ``ref`` is a full branch ref that names one ref exactly.

    ``git ls-remote`` matches its argument as a pattern, so a wildcard would
    answer for a different ref than the one the branch configured. This check
    accepts only complete ``refs/heads/`` names that contain no pattern
    metacharacter, control character, or structure Git forbids.

    Returns
    -------
    bool
        ``True`` only for a literal, complete branch ref.

    """
    if _has_git_ref_structure_error(ref) or _has_control_character(ref):
        return False
    return not any(character in _INVALID_REF_CHARS for character in ref)


def _has_control_character(ref: str) -> bool:
    """Return whether ``ref`` contains an ASCII control character."""
    return any(
        ord(character) < _FIRST_PRINTABLE_CODE_POINT
        or ord(character) == _DELETE_CODE_POINT
        for character in ref
    )


def _has_git_ref_structure_error(ref: str) -> bool:
    """Return whether ``ref`` uses structure Git forbids in ref names."""
    if not ref.startswith("refs/heads/") or ref.endswith(("/", ".")):
        return True
    if any(pattern in ref for pattern in _FORBIDDEN_REF_SEQUENCES):
        return True
    # Git rejects a component that starts with a dot or ends in ``.lock``.
    return any(
        component.startswith(".") or component.endswith(".lock")
        for component in ref.split("/")
    )


def mentions_exact_ref(output: str, ref: str) -> bool:
    """Return whether ``git ls-remote`` output names ``ref`` exactly."""
    return any(line.rpartition("\t")[2] == ref for line in output.split("\n"))


class _LsRemoteRunner(typ.Protocol):
    """Callable that runs one ``git ls-remote`` argument list."""

    def __call__(
        self, arguments: typ.Sequence[str], cwd: str, /
    ) -> subprocess.CompletedProcess[str]:
        """Run ``arguments`` in ``cwd`` and return the completed process."""


def _run_ls_remote(
    arguments: typ.Sequence[str],
    cwd: str,
) -> subprocess.CompletedProcess[str]:
    """Run ``git ls-remote`` with a deadline and a prompt-free environment.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The completed process.

    Raises
    ------
    FileNotFoundError
        When no ``git`` executable can be found.
    OSError
        When the process cannot start.
    subprocess.SubprocessError
        When the query exceeds its deadline.
    UnicodeDecodeError
        When the output cannot be decoded.

    """
    executable = shutil.which("git")
    if executable is None:
        msg = "git is not installed"
        raise FileNotFoundError(msg)
    environment = os.environ.copy()
    environment[_GIT_TERMINAL_PROMPT_ENV] = "0"
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [executable, "ls-remote", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=_LS_REMOTE_TIMEOUT_SECONDS,
        check=False,
        stdin=subprocess.DEVNULL,
        env=environment,
    )


class _RemoteRefProbe:
    """Bounded ``git ls-remote`` query for one configured branch ref."""

    def __init__(
        self,
        working_directory: str,
        *,
        run: _LsRemoteRunner = _run_ls_remote,
    ) -> None:
        """Keep the directory and the runner the query uses."""
        self.working_directory = working_directory
        self._run = run

    def branch_state(self, remote: str, merge_ref: str) -> UpstreamState:
        """Return the upstream state an advertisement reports for ``merge_ref``.

        Parameters
        ----------
        remote : str
            Configured remote name or URL to query.
        merge_ref : str
            Exact branch ref the local branch tracks.

        Returns
        -------
        UpstreamState
            ``present`` when the advertisement names the exact ref, ``gone``
            when a successful advertisement omits it, and ``unknown`` when the
            answer could not be trusted.

        """
        completed = self._ls_remote(("--exit-code", "--heads", remote, merge_ref))
        if completed is None:
            return "unknown"
        if completed.returncode == _LS_REMOTE_NO_MATCH_STATUS:
            return "gone"
        if completed.returncode != 0:
            return "unknown"
        # `ls-remote` patterns match the tail of a ref, so a longer branch such
        # as `other/refs/heads/<name>` can make the command exit 0 without the
        # configured ref existing. Require the exact ref before saying present.
        return "present" if mentions_exact_ref(completed.stdout, merge_ref) else "gone"

    def _ls_remote(
        self,
        arguments: typ.Sequence[str],
    ) -> subprocess.CompletedProcess[str] | None:
        """Run a bounded ``ls-remote``, timing it and recording the outcome.

        Statuses 0 and 2 are successful advertisement queries: one found the
        ref, the other reports that no ref matched. Any other non-zero status
        is a failed query, so it is recorded as a failure rather than being
        reported as a successful answer.

        Returns
        -------
        subprocess.CompletedProcess[str] | None
            The completed query, or ``None`` when it could not run.

        """
        with observability.get_recorder().span("upstream_lookup"):
            try:
                completed: subprocess.CompletedProcess[str] | None = self._run(
                    arguments, self.working_directory
                )
            except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
                completed = None
        error_kind: observability.ErrorKind | None = None
        if completed is None:
            outcome: observability.Outcome = "failure"
            error_kind = "os_error"
        elif completed.returncode in _LS_REMOTE_QUERY_STATUSES:
            outcome = "success"
        else:
            outcome = "failure"
            error_kind = "git_command_error"
        observability.get_recorder().record(
            observability.Observation(
                operation="upstream_lookup",
                outcome=outcome,
                error_kind=error_kind,
            )
        )
        return completed
