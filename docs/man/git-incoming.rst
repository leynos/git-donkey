git-incoming
============
Show commits that would be pulled
---------------------------------

:Manual section: 1
:Manual group: Git-donkey manual

SYNOPSIS
========

**git incoming** [*REF*] [**--no-fetch**]

**git-incoming** [*REF*] [**--no-fetch**]

DESCRIPTION
===========

Print the commits reachable from the comparison ref and not reachable from
``HEAD``.
The result previews what a pull would bring into the current branch without
modifying the working tree.
Mercurial names this direction ``incoming``.

The comparison ref is the optional positional argument *REF*.
When it is omitted, the command uses the current branch's configured upstream,
such as ``origin/feature/foo``.
Without an upstream and without *REF*, the command reports the missing
configuration and exits with code ``2`` instead of comparing the branch with
itself.

When the comparison ref is backed by a remote, the command fetches that remote
before comparing, so the result reflects the remote's current state.
The longest matching remote name wins when one configured remote name is a
prefix of another.
An explicit ``refs/remotes/<remote>/...`` ref fetches ``<remote>``.
A ref that no remote backs is not fetched.
Use **--no-fetch** to compare against the currently known local tracking ref
without contacting the remote.

Output is one line per commit in ``git log --oneline --decorate`` form: the
abbreviated commit hash, any decorations, and the subject.

Exit codes follow Mercurial's documented behaviour for this command, with a
git-donkey-specific code for a command that could not run:

``0``
    At least one incoming commit was found and printed.

``1``
    The comparison ran and found no incoming commits.

``2``
    The command could not run: no upstream is configured and no ref was given,
    the fetch failed, or the comparison failed.

OPTIONS
=======

REF
    Comparison ref, such as ``origin/main`` or ``refs/remotes/origin/main``.
    Defaults to the current branch's upstream.

--no-fetch
    Do not fetch the remote that backs the comparison ref. The command
    compares against the local tracking ref as it currently stands.

-h, --help
    Display command-line help and exit.

--version
    Display the installed version and exit.

EXAMPLES
========

Show commits that the current branch's upstream holds and ``HEAD`` lacks::

    git incoming

Invoke the installed console script directly::

    git-incoming

Compare against an explicit ref, fetching its remote first::

    git incoming origin/main

Compare against the local tracking ref without contacting the remote::

    git incoming --no-fetch

SEE ALSO
========

**git**\ (1), **git-fetch**\ (1), **git-log**\ (1), **git-outgoing**\ (1),
**git-out**\ (1)
