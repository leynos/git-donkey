git-out
=======
Show commits that would be pushed
---------------------------------

:Manual section: 1
:Manual group: Git-donkey manual

SYNOPSIS
========

**git out** [*REF*] [**--no-fetch**]

**git-out** [*REF*] [**--no-fetch**]

DESCRIPTION
===========

Alias of **git-outgoing**\ (1).
It prints the commits reachable from ``HEAD`` and not reachable from the
comparison ref, defaulting to the current branch's upstream and fetching the
remote that backs the ref unless **--no-fetch** is given.
The exit codes are those of **git-outgoing**\ (1): ``0`` when commits were
found, ``1`` when the comparison found none, and ``2`` when the command could
not run.
See **git-outgoing**\ (1) for the full description.

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

Show the commits a push would send to the upstream::

    git out

Invoke the installed console script directly::

    git-out origin/release/1.2

SEE ALSO
========

**git-outgoing**\ (1), **git**\ (1), **git-log**\ (1), **git-in**\ (1)
