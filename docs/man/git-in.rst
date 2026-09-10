git-in
======
Show commits that would be pulled
---------------------------------

:Manual section: 1
:Manual group: Git-donkey manual

SYNOPSIS
========

**git in** [*REF*] [**--no-fetch**]

**git-in** [*REF*] [**--no-fetch**]

DESCRIPTION
===========

Alias of **git-incoming**\ (1).
It prints the commits reachable from the comparison ref and not reachable from
``HEAD``, defaulting to the current branch's upstream and fetching the remote
that backs the ref unless **--no-fetch** is given.
The exit codes are those of **git-incoming**\ (1): ``0`` when commits were
found, ``1`` when the comparison found none, and ``2`` when the command could
not run.
See **git-incoming**\ (1) for the full description.

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

Show the commits a pull would bring into the current branch::

    git in

Invoke the installed console script directly::

    git-in origin/main

SEE ALSO
========

**git-incoming**\ (1), **git**\ (1), **git-log**\ (1), **git-out**\ (1)
