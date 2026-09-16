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
The parent pull request's head is read when there is a parent to identify, and
the tree identity and cumulative patch identity of the range are read when
**--deep** is given.
Those two are inferred evidence, which can never establish a boundary.
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
the commits it would drop, and a replay plan: a ref to keep the child tip
under, then a ``git rebase --onto`` command, in full object IDs, that performs
the replay, and the child tip the answer was computed against.
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

Without **--record** a run changes nothing but the refs it writes under
``refs/wheresat/``: it does not touch the working tree, the index, the stack
record, or any branch.
With **--record** it also writes the child branch's stack record, which is the
anchor ref ``refs/stack-bases/``\ *BRANCH* and the branch's configuration
section.
A boundary that no other ref reaches is retained under
``refs/wheresat/boundary/``\ *BRANCH* before it is reported, so that a later
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
    the local ``refs/remotes/``\ *REMOTE*\ ``/HEAD`` symbolic ref so that the run
    needs no network.
    A run that cannot name one asks for **--onto** rather than guessing
    between a local ``main`` and the remote's idea of it.

--parent OWNER/REPO#N
    Parent pull request, for example ``octocat/hello-world#42``.
    Names the pull request whose head this branch was cut from.
    The pull request is consulted through the forge, and the parent's head it
    names is fetched into a cache ref under ``refs/wheresat/parent-head/``.
    A run that cannot consult the parent it was given, because it is offline,
    holds no usable credential, or the forge could not be reached, exits ``3``
    with the gates that depend on the parent left unanswered.

--remote NAME
    Remote holding the child branch.
    Defaults to the principal remote, which is the first configured one.
    Today it names the remote whose local ``refs/remotes/``\ *NAME*\ ``/HEAD``
    symbolic ref supplies the default target when **--onto** is absent.
    A parent's head is fetched from this remote when the run has a parent whose
    head it needs, and the repository is identified from the remote's URL.

--limit N
    Commits the commit-to-pull-request association search examines: how many
    of the child's newest commits the run asks GitHub about, defaulting to
    ``20``.
    The adapter asks about at most ``20`` commits by its own constant, so a
    value above ``20`` does not widen the search.
    A history longer than the window the search examined is a question left
    unanswered rather than an answer of nothing, so the run exits ``3`` naming
    the bound it stopped at and the way out of it: **--parent**, which names
    the parent directly.

--heuristic-window N
    Trunk commits the deep scan examines when **--deep** is set, defaulting to
    ``200``, counted backwards from the target.
    The report states the window it scanned, and a scan the window cut short is
    reported as a warning naming the window, never as a complete one.

--no-fetch
    Perform no Git transport: the parent's head is not fetched and no cache ref
    is written.
    Queries to the forge are still permitted.
    A head an earlier run already cached is read from the cache, so the option
    forbids transport rather than forbidding the head.
    A run that must fetch and may not says so, and exits ``3``.

--offline
    Perform no network access of any kind, so no forge query runs.
    The run answers from the stack record and local ancestry.
    A parent identification declined because the run is offline is reported as
    skipped, not as a question that could not be put, and it is not a fault.
    A run that was told to consult a named parent still cannot judge the gates
    about that parent, so it exits ``3`` rather than refusing.

--deep
    Also derive tree-identity and cumulative-patch-identity candidates, by
    comparing the child against the target's content.
    They are inferred evidence, which can never establish a boundary: the
    option can add candidates to the report but never change the verdict.
    It is off by default, and the scan it controls is linear in
    **--heuristic-window**.

--explain
    Render the gate table even when a boundary was established.
    The table is always rendered when nothing was established, because the
    gates are then what the reasons are about.

--json
    Emit the versioned machine-readable envelope on standard output instead of
    the text report, on every exit status including ``2``, for an argument the
    parser refuses as well as for a run that fails later. Without it, a refused
    argument is reported by the parser on standard error as it is elsewhere.
    Adding an envelope key in a later revision is permitted; removing or
    retyping one requires a new schema string.

--op-id ID
    Name the per-run evidence namespace under ``refs/wheresat/op/``.
    A test seam: no console run writes per-run refs yet, so the ID is only
    checked for safety.
    An ID must start with a letter or digit and hold only letters, digits,
    dots, hyphens, and underscores, in at most 64 characters, and it must be a
    name Git accepts as a ref path component, so it may not hold ``..``, may
    not end in ``.``, and may not end in ``.lock``.
    Either failure is refused with status ``2`` before the run reads anything.

--record
    Refresh the child branch's stack record from the result, which is the one
    write this command makes outside the evidence namespace.
    Only a boundary the run established from attested evidence is written; a
    run that had to derive its boundary still reports it, and warns that
    nothing was recorded.
    A refresh never invents a record: a branch nobody recorded is reported,
    not recorded.
    It refuses with status ``2`` when the record's anchor ref exists and
    **--expected-old** was not given.

--expected-old OID
    Commit the record's anchor ref must still hold for **--record** to replace
    the record.
    It is required when the anchor ref exists, and refused with status ``2``
    when it is given and the anchor does not exist, or when it is given without
    **--record**, which is the only option that reads it.
    Git performs the same comparison again at the write, so an anchor that
    moves in between is reported as a conflict rather than overwritten.

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

A run that established a boundary prints the partition and the replay. The
commands are shown in full, because they are meant to be pasted, and a backup
ref precedes them so the replay can be undone::

    git wheresat: boundary for issue-123-fix
      child tip   4d5e6f7
      target      7c8d9e0
      boundary    1a2b3c4
    ...
    Replay
      # back up the child tip first, then replay onto the target
      git update-ref refs/wheresat-backup/issue-123-fix 4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e
      git rebase --onto 7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b issue-123-fix
      # undo: git reset --hard refs/wheresat-backup/issue-123-fix

      Verify before running: the child tip must still be 4d5e6f7

SEE ALSO
========

**git**\ (1), **git-rebase**\ (1), **git-donkey**\ (1), **git-plonk**\ (1)
