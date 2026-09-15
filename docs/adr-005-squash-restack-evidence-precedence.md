# Architectural decision record (ADR) 005: squash-restack evidence precedence

## Status

Accepted. Boundary evidence is ranked in three tiers, each candidate must clear
every applicable one of eight named gates, refusal is a first-class outcome
with its own exit code, and an error is never a negative answer.

## Date

2026-09-14

## Context and Problem Statement

`git wheresat` answers one question: which commit is the exclusive replay
boundary for a branch whose parent was squash-merged? The answer is a full
object ID that the user pastes into `git rebase --onto`. If it is wrong the
user either replays work that already landed, or discards work that never did,
and in both cases the damage is silent until much later.

Several kinds of evidence are available, and they are not equally good. A stack
record written at branch birth names the exact commit observed at the moment it
was true. A fetched pull request head is an exact commit too. A merge base is
computed from history that may have been rewritten.
`git merge-base --fork-point` reads a reflog, which expires. Tree identity and
patch identity compare content, and content comparison cannot distinguish a
commit from a later commit that happens to contain the same change.

The procedure this work automates warns specifically that patch and tree
comparisons are forensic evidence rather than an oracle, and that fork-point
output must be validated as a candidate rather than taken as an answer. The
question is how to turn that warning into something a program cannot get wrong.

## Decision Drivers

- The failure that matters is a confidently wrong boundary, because the user
  acts on it. Refusing costs a detour; guessing costs work.
- Some evidence is structurally incapable of being conclusive, and the
  structure should say so rather than relying on a threshold.
- "Cannot tell" and "no" are different answers, and the difference must survive
  all the way to the exit code.
- The user must be able to re-check the answer's premise and to undo the
  command it proposes.
- Every evidence source has a cost, and an unbounded scan of a large repository
  is not an acceptable default.

## Options Considered

### Option A: Three tiers plus a gate conjunction

Candidates are `ATTESTED` (recorded by a deliberate act naming the exact
commit), `DERIVED` (computed from surviving history), or `INFERRED` (suggested
by content comparison). Attested evidence may establish alone; derived evidence
needs a second, independently obtained candidate naming the same commit;
inferred evidence never establishes. Every applicable gate must pass.

### Option B: Two classes, heuristic and non-heuristic

Non-heuristic evidence establishes; heuristic evidence is reported but never
establishes. A frozen set of kinds and one conditional decide which is which.

### Option C: One merged pool with a confidence score

Every candidate contributes a weight and the best-scoring candidate above a
threshold establishes the boundary.

| Topic                            | Option A    | Option B  | Option C  |
| -------------------------------- | ----------- | --------- | --------- |
| Fork-point alone establishes     | no          | yes       | depends   |
| Illegal state representable      | no, by type | yes       | yes       |
| Rule visible at the type level   | yes         | no        | no        |
| Tuning a threshold can weaken it | no          | no        | yes       |
| Explains itself to the user      | per gate    | per class | per score |

_Table 1: comparison of the three options._

## Decision Outcome / Proposed Direction

Option A. The three-tier model lets each rule match the risk it addresses:
attested evidence may establish alone because it was recorded deliberately;
derived evidence requires corroboration because fork-point surprise is the
highest-likelihood hazard in the design and `git merge-base --fork-point`
answers from a reflog that expires; inferred evidence never establishes because
an added change followed by its revert produces a later prefix with the same
tree and the same net patch, and `git patch-id` ignores all whitespace within a
patch.

Option B is rejected on a specific count rather than in general. It classifies
fork-point as establishing, which makes the safety net aim away from the
highest-likelihood hazard. It also implements a promise of structural
incapability as a frozen set and one conditional, guarded only by a property
test — which is the arrangement the same requirement condemns everywhere else.

Option C is rejected because a threshold is a dial, and a dial that can be
turned down is not a structural guarantee.

Two further decisions make the model's guarantees structural rather than
test-enforced. The assessment's `Established` arm holds a tuple of attested or
derived candidates and cannot hold an inferred one, so constructing an illegal
result is a type error. And the gates are evaluated against a `GraphFacts`
record of collected data rather than against a live adapter, so the assessment
is a pure function that a property test can drive over an arbitrary graph.

## Consequences

- A weaker verdict is not a failure. `Unresolved` (exit `1`) reports candidates
  and the gate that stopped them; `Indeterminate` (exit `3`) reports what could
  not be determined. Neither proposes a rebase command.
- Refusal is tested as a first-class outcome rather than as an error path, and
  a repository whose parent was rewritten before merging must refuse.
- The four-code convention departs from the three-code convention used by
  `git incoming` and `git outgoing`. The two commands would file "this
  repository cannot answer" under the same code as "the arguments are wrong",
  which is the machine-readable version of the conflation this record exists to
  prevent.
- The report always names the child tip the answer was computed against, the
  target it was computed against, a backup ref, and full object IDs, so the
  proposed rebase is reversible and its premise re-checkable.
- An error raises `Indeterminate` and stops: the run never falls through to a
  lower evidence tier after a Git or GitHub failure. A `404` for a private
  parent repository is indistinguishable from "no such pull request", and
  treating it as the latter would turn a lost credential into a confident
  answer.
- Expensive evidence is opt-in. `--deep` adds inferred candidates and is a cost
  control, not a semantics switch, so it can add rows to the report and can
  never change the verdict.

## Known Risks and Limitations

- Gate 7 cannot prove the replay range contains only child work, and the patch
  comparison it uses is not injective. It is one gate among eight for that
  reason, and the limitation is stated in the design document rather than
  hidden behind the gate's name.
- Derived corroboration depends on two sources being genuinely independent. The
  independence rule is by source identity, and a source that internally
  consults two reflogs would satisfy it while adding little.
- Attested evidence is trusted once its gates pass. A record written for the
  wrong branch, or written before a rewrite the record does not describe, is
  caught by `record-not-superseded` only when that gate can see the newer
  integration.
- The commit-message trailer alternative, which would have answered the
  rewritten-parent case outright, is rejected in
  [ADR-004](adr-004-shared-stack-records.md) rather than here, and remains the
  recorded fallback if refusal proves too common in practice.
