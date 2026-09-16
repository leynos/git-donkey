"""Where the GitHub token lives, and how one is read back.

``git fafo`` is the only command that obtains a token, and what it obtained it
caches: authorizing is an interaction with a human, so a run that asked for one
every time would be a run nobody automates. ``git wheresat`` reads the same
cache, and reads it without ever prompting, because it must be usable from a
script. Two readers of one file is exactly the arrangement in which a second
copy of the path becomes a second opinion, so the location and the file's shape
live here and neither command spells them for itself: a cached token one of
them cannot find is invisible until an operator is asked to authorize again,
which is the one outcome caching exists to prevent.

Nothing here decides whether a token is usable, and nothing here reports to the
user. A file that is not there is an absent token rather than an error, because
the reader may hold an environment variable instead, and it is the caller that
knows which sources it is willing to try and what to do when they are all
empty.

"""

from __future__ import annotations

import os
import tempfile
import typing as typ
from pathlib import Path

CREDENTIALS_FILE_ENV: typ.Final = "GIT_DONKEY_CREDENTIALS_FILE"
"""Environment variable that moves the credential file from its default path."""

_DEFAULT_CREDENTIALS_FILE = "~/.config/git-donkey/github-token"


def credentials_path() -> Path:
    """Return the configured path for the cached GitHub token.

    Returns
    -------
    Path
        The file named by :data:`CREDENTIALS_FILE_ENV`, or the default path
        when that variable names none.

    """
    raw = os.environ.get(CREDENTIALS_FILE_ENV)
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(_DEFAULT_CREDENTIALS_FILE).expanduser()


def read_token(path: Path) -> str | None:
    """Read the first non-empty token line from ``path`` if available.

    Parameters
    ----------
    path : Path
        Credential file to read, which need not exist.

    Returns
    -------
    str | None
        The token, or ``None`` when the file is absent, unreadable, or holds no
        non-empty first line. The bytes are read as UTF-8, which is what
        :func:`write_token` wrote, and a file that is not that encoding holds no
        token this reader can use — the same answer as a file it cannot open.

    """
    if not path.exists():
        return None

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None

    if not lines:
        return None

    token = lines[0].strip()
    return token or None


def write_token(path: Path, token: str, auth_id: int | None) -> None:
    """Persist a GitHub token and optional authorization id.

    The write is replace-in-place through a temporary file beside the target,
    so a reader never observes a half-written credential and a failure leaves
    the previous one intact. The temporary file holds the token in the clear
    until it is renamed over the target, so a write that fails removes it
    rather than leaving a copy behind.

    Parameters
    ----------
    path : Path
        Credential file to write, with its parent created if it is absent. A
        directory this call creates is created owner-only; an existing one
        keeps whatever mode it already has.
    token : str
        Token to store, on the first line.
    auth_id : int | None
        Authorization id to store on the second line, or nothing when the
        token's origin did not report one.

    Raises
    ------
    OSError
        Propagated from the filesystem when the temporary file cannot be
        written or renamed over the target, after the temporary file has been
        removed and its descriptor closed.

    """
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = f"{token}\n"
    if auth_id is not None:
        payload += f"{auth_id}\n"
    temporary_handle, temporary_name = tempfile.mkstemp(dir=path.parent)
    temporary_path = Path(temporary_name)
    opened = False
    try:
        with os.fdopen(
            temporary_handle,
            "w",
            encoding="utf-8",
        ) as fh:
            opened = True
            fh.write(payload)
        temporary_path.replace(path)
    except OSError:
        if not opened:
            # ``os.fdopen`` refused before it took the descriptor over, so the
            # descriptor is still this call's to close; unlinking the name
            # alone would leave the token readable through it.
            os.close(temporary_handle)
        temporary_path.unlink(missing_ok=True)
        raise
