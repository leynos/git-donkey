# Architectural decision record (ADR) 004: shared stack records

## Status

Accepted. `git donkey`, `git plonk`, and `git wheresat` read and write one
versioned stack record through one pair of owning modules; configuration holds
the record's values and the anchor ref keeps its boundary commit reachable.

## Date

2026-09-14.

## Context and Problem Statement

`git wheresat` recovers the exclusive replay boundary for a branch whose parent
was squash-merged. Its strongest possible evidence is not forensic at all: it
is the observation `git donkey` makes when it creates the branch, at
`git_donkey/donkey_worktrees.py:184-192`, where the base commit is resolved and
frozen and then discarded because nothing records it.

The recovery tooling is therefore built to reconstruct a fact the tool family
held milliseconds earlier. Worse, another command in the same family destroys
what is left. `git plonk --hard` deletes a completed local branch with
`git branch -D` (`git_donkey/plonk.py:182`), and Git removes the branch's
reflog and its entire `branch.<name>` configuration section with the ref. That
is precisely the evidence the forensic ladder depends on.

Two questions follow. Whether the record is worth introducing at all, given
that a forensic ladder is being built anyway; and, if it is, which artefact
holds it, given that the two obvious candidates are destroyed by different
operations.

## Decision Drivers

- The catastrophic failure is a confidently wrong boundary that the user acts
  on, so evidence quality is the first concern, not tool symmetry.
- Branch creation is the only moment at which the parent's identity and the
  frozen base are simultaneously known and exact.
- The forensic ladder is needed regardless, for branches created before this
  decision, branches created by hand or by another tool, and recovery from a
  different clone. Whatever is added now must not foreclose it.
- A persisted format outlives the release that introduced it: branches with a
  record and branches without must both be readable forever.
- Renaming a branch and deleting a branch destroy different artefacts, and
  neither may lose the record silently.

## Options Considered

### Option A: One shared record across the three commands

A single versioned record, written at branch birth by `git donkey`, read and
refreshed by `git wheresat`, and converted to a tombstone by `git plonk` before
it deletes the branch. The format and its pure decisions live in
`git_donkey/stack_records.py`; every read and write goes through
`git_donkey/stack_store.py`. No command parses a key, builds a record ref path,
or decides the lifecycle for itself.

### Option B: A private record format owned by `git wheresat`

`git wheresat` writes its own record, and `git donkey` writes the same facts in
whatever form it finds convenient. The two are kept in step by convention.

### Option C: Commit-message trailers instead of a record

`git donkey` installs a `prepare-commit-msg` hook that stamps `Stack-Parent:`
and `Stack-Base:` trailers onto the child's first commit, after Gerrit's
`Change-Id` and Jujutsu's change IDs. The relationship then travels inside the
object graph.

| Topic                    | Option A              | Option B              | Option C          |
| ------------------------ | --------------------- | --------------------- | ----------------- |
| Survives a fresh clone   | no                    | no                    | yes               |
| Survives parent deletion | yes, by tombstone     | yes, if honoured      | yes               |
| Mutates commit messages  | no                    | no                    | yes, irreversibly |
| Helps existing branches  | no                    | no                    | no                |
| Divergence risk          | one format, one store | three private parsers | one grammar       |

_Table 1: comparison of the three options._

## Decision Outcome / Proposed Direction

Option A. `git donkey` writes the record when, and only when, it creates a
branch from a base that is not the trunk; `git wheresat` reads it as its
highest-precedence evidence; `git plonk` tombstones before deleting, sweeps
records whose branch has gone, and prunes tombstones after 90 days.

Option B is rejected because three private parsers of one artefact diverge
silently: the writer and the reader disagree about a key name and the reader
reports a missing record rather than a bug. The record's whole value is that a
reader that did not write it can still trust it.

Option C is the strongest alternative and is recorded rather than dismissed. It
is better on durability: trailers survive a fresh clone, a different machine, a
fork-based pull request, a parent force-push, branch deletion,
`git plonk --hard`, and reflog expiry, and they answer the rewritten-parent
case this command must otherwise refuse. It is rejected because it irreversibly
mutates commit messages, because the recovery procedure this work automates
states that no per-commit prefix is necessary, and decisively because it offers
nothing for the branches that already exist — which is the acute problem. It is
the alternative to revisit if the record proves insufficient in practice.

Within Option A, the artefact split is forced by measurement rather than chosen
for symmetry:

- `git branch -D <name>` deletes the entire `branch.<name>` configuration
  section, including keys Git does not define.
- `git branch -m <old> <new>` carries that section to the new name and does not
  move `refs/stack-bases/<old>`.

So configuration is authoritative for the record's **values**, and the anchor
ref is authoritative only for **reachability**. Where they disagree the record
is reported malformed and never resolved by preferring one. The anchor has a
second job that falls out of the same design: it keeps the boundary commit
reachable, so `git gc` cannot collect the object a surviving child depends on.

A branch with no record is still entombed when `git plonk` deletes it. A parent
created from the trunk is not itself stacked and so has no record, yet its tip
is exactly what a surviving child needs, and the deletion is forced with
`git branch -D`, so there is no refusal to fall back on.

## Consequences

- The forensic ladder serves a shrinking population — branches predating the
  record, branches made by hand or by another tool, and other clones — rather
  than a growing one.
- `git donkey` and `git plonk` change, but only in what they record: neither
  learns anything about pull requests, squash merges, or evidence tiers.
- Three commands now share a persisted format, so the format is versioned from
  its first commit and every reader treats "absent" as a first-class state.
- The record namespace must remain a subset of the branch namespace, because a
  Git reference and a reference directory cannot share a path.
- A record is local to one clone; cross-clone recovery is left to the shared
  record written in a pull request body.

## Known Risks and Limitations

- A tombstone preserves the tip, not the reflog, so fork-point recovery for a
  deleted parent is still lost. The tip is what the parent-identity and
  intactness checks actually need.
- The record namespace's safety rests on the subset invariant rather than on a
  collision-free layout; an orphaned record eventually collides with a nested
  branch name. The sweep maintains the invariant and the writer reports a
  collision instead of crashing.
- The namespace layout is flat rather than leaf-suffixed. A layout such as
  `refs/stack-bases/<branch>/base` would make collisions impossible by
  construction and remains the recorded fallback if the subset proves fragile.
- Records are invisible to `git status` and to a fresh clone, so a user who
  copies a repository directory without its configuration loses them.
