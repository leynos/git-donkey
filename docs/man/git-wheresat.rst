git-wheresat
============
Locate a branch's replay boundary
---------------------------------

:Manual section: 1
:Manual group: Git-donkey manual

SYNOPSIS
========

**git wheresat** [**--branch** *NAME*] [**--onto** *REV*]
[**--parent** *OWNER/REPO#N*] [**--remote** *NAME*] [**--limit** *N*]
[**--heuristic-window** *N*] [**--no-fetch**] [**--offline**] [**--deep**]
[**--explain**] [**--json**] [**--op-id** *ID*] [**--record**]
[**--expected-old** *OID*]

**git-wheresat** [*options*]

DESCRIPTION
===========

Report where a branch was replayed over: the commit it should be rebased onto
so that work already landed upstream is dropped and the work it still carries
is kept.
Run the command inside a Git repository, and by default it reads the branch
currently checked out.

A branch created by **git donkey** records the commit its worktree was branched
from, at the moment of branching, under the branch's own configuration.
That record is the strongest evidence a run can read, and every other source
is weighed below it: the merge base with the target and the fork point.
The parent pull request's head, the tree identity of the child's commits, and
the cumulative patch identity of the range are not read yet: no forge query
runs, and no deep comparison is made.
A boundary is established only from one deliberate record, or from two
independent sources computed from history that agree with each other.
Content comparison alone never establishes a boundary.
A commit a deliberate statement names outranks one computed from surviving
history, so a lower-ranked candidate stops being an answer as soon as one at a
stronger tier exists; two candidates left at the same tier are an ambiguity, and
the run refuses rather than choosing between them.
A record whose attested claim a later integration superseded is not discarded:
the record is read as derived evidence, so it may still support the commit it
names once another source agrees with it.

The report names the boundary, the commits the branch still carries above it,
the commits it would drop, and a ``git rebase --onto`` command that performs
the replay.
The command never rebases anything itself.

The command exits with one of the following statuses:

``0``
    A boundary was established.

``1``
    The boundary could not be established from complete evidence.
    This is a legitimate answer about the evidence rather than a malfunction,
    and the report names what refused.

``2``
    The command could not run, for example a branch or target that does not
    resolve, or a malformed ``--parent``.

``3``
    The result is indeterminate: a question the procedure asked could not be
    answered, because the repository could not answer it.
    The environment needs repair before the run can conclude anything.

These options are accepted and have no effect yet: **--limit**,
**--heuristic-window**, **--no-fetch**, **--offline**, **--deep**, **--record**,
and **--expected-old**.
No run fetches evidence, queries a forge, compares deeply, or writes a record,
so none of them changes the answer or the repository.

A run changes nothing but the refs it writes under ``refs/wheresat/``: it does
not touch the working tree, the index, the stack record, or any branch.
A boundary that no other ref reaches is retained under
``refs/wheresat/boundary/``*BRANCH* before it is reported, so that a later
``git gc`` cannot take with it an answer the report has already given.

The report warns about the worktree holding the branch when that worktree would
not accept the replay the report prints: uncommitted changes, or an operation
already in progress there, which is a rebase, a merge, a cherry-pick, a revert,
or a bisect.
A worktree whose state could not be read is warned about as well, because a run
that warned about nothing would be read as a run with nothing to warn about.
A warning changes neither the verdict nor the exit status: the state it
describes is not evidence about the boundary.

OPTIONS
=======

--branch NAME
    Child branch to read.
    Defaults to the branch checked out in the current directory.

--onto REV
    Replay target.
    Defaults to the default branch the principal remote advertises, read from
    the local ``refs/remotes/``*REMOTE*``/HEAD`` symbolic ref so that the run
    needs no network.
    A run that cannot name one asks for **--onto** rather than guessing
    between a local ``main`` and the remote's idea of it.

--parent OWNER/REPO#N
    Parent pull request, for example ``octocat/hello-world#42``.
    Names the pull request whose head this branch was cut from.
    No forge query runs yet, so the pull request cannot be consulted, and a
    run that names one exits ``3`` with the gates about a parent unanswered.

--remote NAME
    Remote holding the child branch.
    Defaults to the principal remote, which is the first configured one.
    Today it names the remote whose local ``refs/remotes/``*NAME*``/HEAD``
    symbolic ref supplies the default target when **--onto** is absent.
    No parent's head is fetched from it, because no forge query runs yet.

--limit N
    Commits the commit-to-pull-request association search would examine,
    defaulting to ``20``.
    That search is not wired yet, so the option has no effect; the report caps
    the commits it lists per range at a separate, fixed ``20``.

--heuristic-window N
    Trunk commits the deep scan would examine when **--deep** is set,
    defaulting to ``200``, counted backwards from the target.
    No such scan runs yet, so the option has no effect.
    When one runs, the report will state the window it read, so that a partial
    scan never reads as a complete one.

--no-fetch
    Perform no Git transport, while still permitting queries to the forge.
    Neither transport nor forge query runs yet, so the option has no effect.

--offline
    Perform no network access of any kind.
    No path attempts network access yet, with or without the option, so it
    produces the same local-evidence answer either way.
    Once any path attempts access, the stack record and local ancestry must
    suffice, or the command will exit ``3`` naming the gates it could not
    evaluate.

--deep
    Also derive tree-identity and cumulative-patch-identity candidates.
    Those candidates are not derived yet, so the option has no effect.
    They are inferred evidence, which can never establish a boundary: the
    option can add candidates to the report but never change the verdict.
    It is off by default because the scan it controls would be linear in
    **--heuristic-window**.

--explain
    Render the gate table even when a boundary was established.
    The table is always rendered when nothing was established, because the
    gates are then what the reasons are about.

--json
    Emit the versioned machine-readable envelope on standard output instead of
    the text report, on every exit status including ``2``.
    Adding an envelope key in a later revision is permitted; removing or
    retyping one requires a new schema string.

--op-id ID
    Name the per-run evidence namespace under ``refs/wheresat/op/``.
    A test seam: the run fetches no evidence yet, so it writes no per-run
    refs, and the ID is only checked for safety.
    An ID must start with a letter or digit and hold only letters, digits,
    dots, hyphens, and underscores, in at most 64 characters; anything else is
    refused with status ``2`` before the run reads anything.

--record
    Refresh the child branch's stack record from the result, which would be
    the one write this command makes outside the evidence namespace.
    No record is written yet, so the option has no effect and raises no error.
    When wired, it will refuse unless the boundary was established from
    attested evidence.

--expected-old OID
    Commit the existing stack record will have to name for **--record** to
    replace it, so that a record written by another run will be reported as a
    conflict rather than overwritten.
    No record is written yet, so the option has no effect.

-h, --help
    Display command-line help and exit.

--version
    Display the installed version and exit.

EXAMPLES
========

Report the boundary of the branch checked out here::

    git wheresat

Report the boundary of another branch against a named target::

    git wheresat --branch issue-123-fix --onto origin/main

Show the gate table beside an established boundary::

    git wheresat --explain

Read the boundary from local evidence alone, with no network access::

    git wheresat --offline

Emit the envelope for a script to consume::

    git wheresat --json

A run that established a boundary prints the partition and the replay::

    git wheresat: boundary for issue-123-fix
      child tip   4d5e6f7a
      target      7c8d9e0f
      boundary    1a2b3c4d
    ...
    Replay
      git rebase --onto 7c8d9e0f 1a2b3c4d issue-123-fix

SEE ALSO
========

**git**\ (1), **git-rebase**\ (1), **git-donkey**\ (1), **git-plonk**\ (1)
