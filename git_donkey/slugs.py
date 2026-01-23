"""Slug generation utilities for git-donkey.

Provides slug generation based on Adler-32 checksums for creating stable,
collision-resistant directory names from repository URLs and branch names.
"""

from __future__ import annotations

import base64
import struct
import zlib

from slugify import slugify


def adler32_base32_lc(text: str, *, strip_padding: bool = True) -> str:
    """Return the Base32 encoding of the Adler-32 checksum of `text`.

    The `text` is encoded as UTF-8 prior to hashing. The checksum is encoded
    as 4 bytes in big-endian order before Base32 encoding.

    Parameters
    ----------
    text : str
        The input text to hash.
    strip_padding : bool
        Whether to strip padding characters (=) from the Base32 output.

    Returns
    -------
    str
        Lower-case Base32-encoded Adler-32 checksum.

    Raises
    ------
    TypeError
        If text is not a str.

    """
    if not isinstance(text, str):
        msg = f"text must be str, got {type(text).__name__}"
        raise TypeError(msg)

    checksum = zlib.adler32(text.encode("utf-8")) & 0xFFFFFFFF
    raw = struct.pack(">I", checksum)  # stable 4-byte representation
    out = base64.b32encode(raw).decode("ascii").lower()
    return out.rstrip("=") if strip_padding else out


def slug_dash_adler32(text: str) -> str:
    """Return a slug combining slugified text with its Adler-32 checksum.

    Format: <slugified-text>-<adler32_base32_lc(original-text)>

    Slugification uses python-slugify's `slugify()`. The checksum is computed
    from the original (un-slugified) text.

    For empty input (text == ""), slugify("") returns "", so the function
    returns "-<checksum>" (a leading dash followed by the checksum from
    adler32_base32_lc("")).

    Parameters
    ----------
    text : str
        The input text to slug.

    Returns
    -------
    str
        Slugified text with appended checksum. For empty strings, returns
        "-<checksum>".

    Raises
    ------
    TypeError
        If text is not a str.

    """
    if not isinstance(text, str):
        msg = f"text must be str, got {type(text).__name__}"
        raise TypeError(msg)

    return f"{slugify(text)}-{adler32_base32_lc(text)}"
