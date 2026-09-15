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


def _refuse_at(monkeypatch: pytest.MonkeyPatch, target: object, name: str) -> None:
    """Make ``name`` on ``target`` report the failure a full disk reports."""

    def refuse(*args: object, **kwargs: object) -> typ.NoReturn:
        """Report the failure the failing step is being made to have."""
        raise OSError(28, _FULL_DISK)

    monkeypatch.setattr(target, name, refuse)


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
    """A failure before the rename removes the file the payload went to."""
    path = _stored(tmp_path)
    _refuse_at(monkeypatch, os, "fdopen")

    with pytest.raises(OSError, match=_FULL_DISK):
        github_credentials.write_token(path, _INCOMING, None)

    assert _beside(path) == [path.name], (
        "a write that stopped before the rename is cleaned up like one that "
        "stopped at it"
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
