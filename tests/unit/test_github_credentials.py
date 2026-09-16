"""What a failed credential write leaves on disk.

The credential file is written through a temporary file beside it, so that a
reader never observes a half-written token and a failure leaves the previous
credential intact. The temporary file holds the token in the clear until it is
renamed over the target, which is what makes the failure points worth pinning:
a write that stops at either of them must publish nothing *and* leave no copy
of the token behind for a later reader of the directory to find.

Where the token comes from, and which of the several sources wins, is
``tests/unit/test_fafo_token.py``'s subject; this module is about the file the
token is finally put in, and about reading it back, because a file no reader can
recover a token from is a write that did not happen.
"""

from __future__ import annotations

import os
import pathlib
import typing as typ

import pytest

from git_donkey import github_credentials

_PREVIOUS = "ghu_the-token-that-was-already-there"
_INCOMING = "ghu_the-token-the-failed-write-was-to-store"
_FULL_DISK = "No space left on device"


def _refuse_at(
    monkeypatch: pytest.MonkeyPatch,
    target: object,
    name: str,
    *,
    arguments: list[object] | None = None,
) -> None:
    """Make ``name`` on ``target`` report the failure a full disk reports.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture that undoes the substitution after the case.
    target : object
        Object the failing step is reached through.
    name : str
        Attribute of ``target`` to make fail.
    arguments : list[object], optional
        List the failing call's first positional argument is appended to, for
        a case that has something to assert about what it was handed.

    """

    def refuse(*args: object, **kwargs: object) -> typ.NoReturn:
        """Report the failure the failing step is being made to have."""
        if arguments is not None and args:
            arguments.append(args[0])
        raise OSError(28, _FULL_DISK)

    monkeypatch.setattr(target, name, refuse)


def _interrupt(*args: object, **kwargs: object) -> typ.NoReturn:
    """Report the interrupt a case makes the write meet."""
    raise KeyboardInterrupt


def _is_open(descriptor: int) -> bool:
    """Return whether ``descriptor`` is still open in this process."""
    try:
        os.fstat(descriptor)
    except OSError:
        return False
    return True


def _stored(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return a credential file holding the previous token, in ``tmp_path``."""
    path = tmp_path / "credentials"
    path.write_text(f"{_PREVIOUS}\n", encoding="utf-8")
    return path


def _beside(path: pathlib.Path) -> list[str]:
    """Return the names of everything beside ``path`` in its directory."""
    return sorted(entry.name for entry in path.parent.iterdir())


def test_a_write_that_cannot_publish_keeps_the_previous_token(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rename the filesystem refuses keeps the credential it was to replace."""
    path = _stored(tmp_path)
    _refuse_at(monkeypatch, pathlib.Path, "replace")

    with pytest.raises(OSError, match=_FULL_DISK):
        github_credentials.write_token(path, _INCOMING, None)

    assert path.read_text(encoding="utf-8") == f"{_PREVIOUS}\n", (
        "the credential the write was to replace is the one still there"
    )


def test_a_write_that_cannot_publish_leaves_no_copy_of_the_new_token(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The temporary file held the token in the clear, so it is removed."""
    path = _stored(tmp_path)
    _refuse_at(monkeypatch, pathlib.Path, "replace")

    with pytest.raises(OSError, match=_FULL_DISK):
        github_credentials.write_token(path, _INCOMING, None)

    assert _beside(path) == [path.name], (
        "the directory holds the credential and nothing the failed write left"
    )


def test_a_write_that_cannot_open_leaves_no_copy_of_the_new_token(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure before the rename removes the file the payload went to.

    The file the descriptor names is removed, but removing a name does not
    release the bytes behind it: the descriptor the temporary file was opened
    with is the one thing that can still read the token until it is closed, so
    a write that stopped here closes it as well as unlinking the path.
    """
    path = _stored(tmp_path)
    opened: list[object] = []
    _refuse_at(monkeypatch, os, "fdopen", arguments=opened)

    with pytest.raises(OSError, match=_FULL_DISK):
        github_credentials.write_token(path, _INCOMING, None)

    assert _beside(path) == [path.name], (
        "a write that stopped before the rename is cleaned up like one that "
        "stopped at it"
    )
    assert len(opened) == 1, (
        "the write should have had one descriptor to open, so the assertion "
        "below is about the file the token was written to"
    )
    assert not _is_open(typ.cast("int", opened[0])), (
        "the descriptor the temporary file was opened with is closed, so "
        "nothing can read the token through the removed file"
    )


def test_an_interrupted_write_leaves_no_copy_of_the_new_token(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write stopped by anything at all is cleaned up like one that failed.

    An interrupt is not an ``OSError``, so cleanup reached only from an
    ``OSError`` handler would leave the temporary file — and the token in the
    clear inside it — beside the credential until the process ended. The
    previous credential is still the one in place, because the interrupt
    arrived before the rename could publish the new token.
    """
    path = _stored(tmp_path)
    monkeypatch.setattr(pathlib.Path, "replace", _interrupt)

    with pytest.raises(KeyboardInterrupt):
        github_credentials.write_token(path, _INCOMING, None)

    assert _beside(path) == [path.name], (
        "the temporary file is removed however the write stopped, so the token "
        "is not left in the clear beside the credential"
    )
    assert path.read_text(encoding="utf-8") == f"{_PREVIOUS}\n", (
        "an interrupt before the rename leaves the credential it was to replace"
    )


def test_a_write_that_succeeds_leaves_only_the_credential(
    tmp_path: pathlib.Path,
) -> None:
    """The path a successful write takes is the one nothing is left behind by."""
    path = _stored(tmp_path)

    github_credentials.write_token(path, _INCOMING, 7)

    assert path.read_text(encoding="utf-8") == f"{_INCOMING}\n7\n", (
        "the token and the authorization id are both readable from the file"
    )
    assert _beside(path) == [path.name], (
        "the temporary file is renamed rather than left beside the credential"
    )


def test_a_file_this_reader_cannot_decode_holds_no_token(
    tmp_path: pathlib.Path,
) -> None:
    """Bytes that are not the UTF-8 the writer wrote read as an absent token.

    The two halves of the file's contract are written and read as UTF-8, so a
    file this reader cannot decode is one it has no token from rather than a
    failure of the run: the reader may hold an environment variable instead,
    and the caller is what decides what an empty set of sources means.
    """
    path = tmp_path / "credentials"
    path.write_bytes(b"\xff\xfe\x00not-a-token")

    assert github_credentials.read_token(path) is None, (
        "an undecodable credential is an absent one, not a refusal to read"
    )
