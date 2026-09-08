git-track
=========
Fetch and switch to a tracking branch
------------------------------------

:Manual section: 1
:Manual group: Git-donkey manual

SYNOPSIS
========

**git track** *BRANCH*

**git-track** *BRANCH*

DESCRIPTION
===========

Fetch the first remote, then switch to an existing local branch and merge
from its remote counterpart, or create a new branch that tracks
``remote/branch``.
When the remote branch does not exist, suggest close matches.
Run the command inside a Git repository.

OPTIONS
=======

BRANCH
    Name of the branch to check out or create from the first remote.

-h, --help
    Display command-line help and exit.

--version
    Display the installed version and exit.

EXAMPLES
========

Fetch and switch to a topic branch::

    git track feature/example

Invoke the installed console script directly::

    git-track release/1.2

SEE ALSO
========

**git**\ (1), **git-fetch**\ (1), **git-merge**\ (1), **git-donkey**\ (1)
