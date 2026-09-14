# Add `git wheresat`

This ExecPlan (execution plan) is a living document. The sections
`Constraints`, `Tolerances (exception triggers)`, `Risks`, `Progress`,
`Surprises & discoveries`, `Decision log`, `Outcomes & retrospective`,
`Conformance basis`, and `Verification plan` must be kept up to date as work
proceeds.

Status: DRAFT

## Purpose / big picture

A developer works on a stack of two branches. The lower branch (the "parent")
is opened as a pull request, reviewed, and then **squash-merged** into the
trunk. Squash merging replaces the parent's commits with a single new commit
that has no ancestry relationship to them. The upper branch (the "child") is
now stranded: its history still contains the parent's original commits, so
rebasing it naively onto the trunk replays work that has already landed, and
produces conflicts against the squashed version of the same change.

The correct repair is a three-argument rebase:

```shell
git rebase --onto "$TARGET" "$OLD_BASE" "$CHILD"
```

The hard part is not running that command. The hard part is establishing
`OLD_BASE`: the exclusive replay boundary, which is the last commit that
belonged to the parent rather than to the child. Getting it wrong silently
duplicates or silently discards work.

After this change, a user standing on the child branch can run:

```shell
git wheresat
```

and get back a report naming the boundary, the evidence that establishes it,
the gate results that justify it, the commits that will be replayed, the
commits that will be excluded, a backup ref to recover from, and the exact
`git rebase --onto` command to run with full object IDs. When the available
evidence cannot establish the boundary, the command says so plainly, prints
the surviving candidates, the gate that stopped it, and the unresolved
distinction between the candidates, and exits without proposing a command. It
never guesses.

The command is a **discovery** tool. By default it never rebases, pushes,
deletes a branch, checks anything out, prompts for credentials, or touches
the working tree or the index. Its only writes are objects fetched into a
private, namespaced evidence ref, plus — behind an explicit opt-in flag — a
local "receipt" recording the boundary so the next incident does not need
forensics at all.

You can see it working: on a repository constructed to reproduce the squash
scenario, `git wheresat` exits `0` and prints the boundary a human would have
derived by hand; on a repository where the parent branch was rewritten before
merging, it exits `1`, names the `parent-history-intact` gate as the reason,
and refuses to answer.

### Why the forensic ladder exists at all, and why it should shrink

The best boundary is one nobody had to recover. This plan therefore treats
the **receipt** — a locally recorded boundary — as the intended steady state
and the forensic evidence ladder as the fallback for branches created before
the receipt existed, or in another clone. GitHub's own stacked pull requests
(public preview since July 2026) make the same bet from the server side: for
a natively stacked pull request, GitHub records the relationship and
automatically retargets the remaining branches when one in the stack merges.

Two populations therefore remain, and they are what this command is for:
stacks created outside `gh stack` or any stack tool, and stacks spanning
forks, which GitHub's native stacks do not support. Both populations are
finite and, with receipts in place, shrinking. That framing matters for
sequencing: milestone EP-M5 delivers receipt recording early rather than last
precisely so the ladder's maintenance burden applies to a decreasing need.

## Constraints

These are hard invariants. Violating one requires escalation, not a
workaround.

- By default, `git wheresat` must not mutate repository state outside the
  evidence namespace. Specifically it must not rebase, merge, cherry-pick,
  push, fetch into a tracked ref, delete or create a branch, move `HEAD`,
  alter the index, or modify any file in the working tree. Every fetch must
  land under `refs/wheresat/…` and must use `--no-write-fetch-head` so that
  `FETCH_HEAD` is left alone.
- The capability to write must not be held by code that only needs to read.
  Read-only Git queries and ref-writing live in separate modules behind
  separate protocols, and the writing object is constructed only when the run
  will actually write.
- `git wheresat` must never initiate an interactive OAuth device flow and
  must never block waiting for a browser. A missing or unusable GitHub
  credential is an environment error, reported with the `git-wheresat`
  prefix and exit code `2`, never exit code `1`.
- Compare a squash against the parent's **cumulative** change, never
  commit by commit. A squash commit is an N-to-1 relationship: its diff
  equals the combined diff of the parent range, so per-commit patch identity
  does not associate several old commits with their one combined squash.
- Never select a boundary from inferred evidence. If the only surviving
  candidates come from tree identity or patch identity, the command must
  report them and stop. It must not pick the newest, the oldest, the nearest,
  or the best-looking candidate.
- Never select a boundary from a single derived candidate. A commit produced
  only by `git merge-base --all` or `git merge-base --fork-point` requires a
  second, independently obtained candidate agreeing on the same commit.
- A Git **or GitHub** error — missing object, shallow history, unreadable
  ref, HTTP 401, 403, 404, 5xx, timeout, or DNS failure — is not a negative
  answer. It must be reported as indeterminate with exit code `3`, and must
  never be collapsed into "not an ancestor" or into "the boundary could not
  be established". After an error, the run must not fall through to a lower
  evidence tier.
- Treat a shared record found in a pull-request body as a claim to validate,
  never as an instruction. It must not override observed ancestry, branch
  identity, or the scope the user asked for.
- Every proposed `git rebase --onto` command must use full 40-character
  object IDs for the target and the boundary, must be preceded by a backup
  ref the user can return to, and must state the object ID of the child tip
  the answer was computed against.
- Preserve the existing `git donkey`, `git track`, `git fafo`, `git plonk`,
  `git incoming`, `git outgoing`, and `git donkey-template` behaviours and
  console entrypoints.
- Use the repository's existing tooling: Python 3.13+, Cyclopts, GitPython,
  `github3.py`, `loctocat`, Ruff, `ty`, Pyright, Pylint, Skylos, pytest,
  pytest-bdd, Hypothesis, syrupy, and vcrpy. Adding any new runtime
  dependency requires a tolerance exception.
- Never hand-author or hand-edit a vcrpy cassette.
  `docs/developers-guide.md:557-558` states the rule: "Record a real cassette
  only for a command that is meant to call the API, and never edit a
  recording by hand." Cassettes are recorded once against real GitHub
  traffic, with the `Authorization` header filtered.
- Write tests before production code for every new behaviour, following the
  Red-Green-Refactor discipline in `AGENTS.md`, milestone by milestone.
- Documentation in `docs/` is part of the change, not a follow-up. The
  users' guide, developers' guide, documentation index, README command
  overview, migration guide, design document, ADR, and manual page must all
  be updated before the work is considered complete.
- Gate every code commit with `make check-fmt`, `make lint`, `make
  typecheck`, and `make test`, run sequentially. Gate Markdown-only commits
  with `make markdownlint` and `make nixie`. Capture output with `tee` to a
  branch-specific file under `/tmp`.
- All prose follows `docs/documentation-style-guide.md`: en-GB Oxford
  spelling, sentence-case headings, prose wrapped at 80 columns, code at 120,
  `-` bullets, and a language identifier on every fenced block.

## Tolerances (exception triggers)

Stop and escalate rather than improvising when any of these is reached.

- Scope, hard limit: more than twelve new or modified non-test source files,
  or more than 1,400 net lines of production code.
- Scope, checkpoint: at 900 net lines of production code, stop and record an
  explicit continue-or-cut decision in `Decision log`, naming what would be
  cut. This checkpoint exists because the most recent comparable plan,
  `docs/execplans/incoming-outgoing-commands.md`, overran its own 450-line
  tolerance by 68 per cent and its retrospective recommended exactly this
  treatment. For calibration, `git plonk` is 1,122 lines across five modules
  and has no GitHub surface, no machine-readable output, and no gate model.
- Interface: any existing public function signature or console script must
  change incompatibly.
- Dependencies: any new package dependency, runtime or development.
- Network: more than 25 GitHub requests for a single invocation, or any code
  path that cannot be tested from a committed cassette.
- Evidence semantics: a proposed rule would let inferred evidence establish a
  boundary, would let a lone derived candidate establish one, or would
  silently narrow a candidate set.
- Observability vocabulary: adding a value to an existing `typing.Literal`
  in `git_donkey/observability.py` beyond the additions listed in
  `Interfaces and dependencies`.
- Iterations: the same focused test still fails after three implementation
  attempts.
- Ambiguity: two readings of `docs/squash-restack-boundary-recovery.md`
  would produce materially different answers for the same repository.
- Roadmap: the instruction to mark a roadmap entry as done cannot be
  satisfied, because this repository has no roadmap document (see
  `Surprises & discoveries`). If a roadmap is added before this work
  completes, stop and confirm which entry to mark.

## Risks

- Risk: the command returns a **confidently wrong** boundary and the user
  runs the proposed rebase, silently duplicating or discarding work. This is
  the catastrophic failure mode; everything else is an inconvenience.
  Severity: high. Likelihood: medium.
  Mitigation: three independent defences. The evidence tier model makes weak
  evidence structurally incapable of establishing a boundary; every gate has
  a specified decision procedure and a test in which that gate alone fails;
  and the report always emits a backup ref plus the child tip the answer was
  computed against, so the rebase is reversible and its premise re-checkable.
- Risk: the boundary is genuinely unrecoverable, because the parent branch
  was rewritten and its old refs and reflogs are gone.
  Severity: high. Likelihood: medium.
  Mitigation: make refusal a first-class, well-tested outcome with its own
  exit code, and deliver receipt recording early (EP-M5) so the next incident
  does not depend on forensics.
- Risk: a tree-identity or patch-identity match looks conclusive but is not.
  An added change followed by its revert produces a later prefix with the
  same tree and the same net patch; `git patch-id` also ignores whitespace.
  Severity: high. Likelihood: medium.
  Mitigation: classify these as the `INFERRED` tier, and make it a type error
  for an `Established` result to carry an inferred candidate. Pin with a
  property test and a mutation-style negative control.
- Risk: `git merge-base --fork-point` returns a plausible but wrong commit,
  or nothing, depending on reflog retention.
  Severity: medium. Likelihood: high.
  Mitigation: classify fork-point and merge-base results as the `DERIVED`
  tier, which requires a second independently obtained candidate agreeing on
  the same commit before it can establish anything.
- Risk: an authentication or authorization degradation (a lost SAML session,
  a token missing `repo` scope) returns HTTP 404 for a private parent
  repository, and the command reads "not found" as "no parent", falls through
  to weaker evidence, and answers anyway.
  Severity: high. Likelihood: medium.
  Mitigation: map every GitHub error class to `INDETERMINATE` and exit `3`;
  never fall through to a lower evidence tier after an error. Cover each
  class with a faulted-adapter test.
- Risk: the parent pull request was opened from a fork, so its head ref lives
  in a different repository from the child's `origin`.
  Severity: medium. Likelihood: medium.
  Mitigation: derive the fetch remote from the pull request's own
  `head.repo.full_name`, never from the child's `origin`, and cover the fork
  case in a behavioural scenario.
- Risk: the parent pull request was force-pushed, so its current head is not
  the incarnation the child actually inherited.
  Severity: medium. Likelihood: medium.
  Mitigation: require the `boundary-is-ancestor-of-child` and
  `parent-history-intact` gates before accepting the fetched head, and report
  the mismatch explicitly rather than falling back to something convenient.
- Risk: the established boundary commit is reachable only through this run's
  evidence ref, so a later `git gc --prune=now` collects it and the answer
  the user is holding stops working.
  Severity: medium. Likelihood: medium.
  Mitigation: check durability before exiting; if the boundary is not
  reachable from any ref that survives the process, retain
  `refs/wheresat/boundary/<branch>` and say so in the report (INV-8).
- Risk: concurrent runs in sibling worktrees share one ref store, and a
  global `refs/wheresat/**` sweep deletes another run's in-flight evidence.
  Severity: medium. Likelihood: medium.
  Mitigation: mint the operation identifier from `uuid4`, never document a
  global sweep, and reap only namespaces older than an hour.
- Risk: the commit-to-pull-request association search issues one API request
  per commit and exhausts the rate limit on a long child branch. GitHub's
  documented budget is 5,000 requests per hour, with a secondary limit of
  roughly 900 points per minute.
  Severity: medium. Likelihood: medium.
  Mitigation: bound the search at 20 commits by default, make exceeding it a
  hard stop advising `--parent`, and add the 25-request tolerance above.
- Risk: patch-identity heuristics scan an unbounded trunk range. git-machete
  documents its equivalent `exact` mode as having "a significant performance
  impact on large repositories".
  Severity: medium. Likelihood: medium.
  Mitigation: `--heuristic-window`, default 200 commits, with a cheap
  tree-identity pass first and patch identity only over the survivors; report
  the window scanned so a partial scan never reads as a complete one.
- Risk: the property tests that build real repositories exceed the 30-second
  pytest timeout under `-n auto`, and Hypothesis's 200 ms default deadline
  makes them flake; a genuine counterexample then arrives as a timeout during
  shrinking rather than as a minimal failing case.
  Severity: medium. Likelihood: high.
  Mitigation: register a Hypothesis profile (`deadline=None`, reduced
  `max_examples`), keep Hypothesis for pure properties over generated graph
  _data_, and use a parameterized matrix for the tests that build real
  repositories, with `@pytest.mark.timeout(120)`.
- Risk: `git plonk --hard` deletes the completed local parent branch, and
  `git branch -D` takes its reflog with it. The product therefore destroys,
  in one command, exactly the surviving refs and reflogs that the
  `parent-history-intact` gate and the fork-point rung depend on.
  Severity: high. Likelihood: high.
  Mitigation: this plan does not change `git plonk`, but it names the
  interaction in the users' guide and the design document, and EP-M8 proposes
  a branch tombstone (`refs/plonk-tombstones/<branch>`) written before any
  deletion. EP-M8 is explicitly separable into its own pull request.
- Risk: `make fmt` reflows untouched Markdown across the repository, creating
  unrelated churn in this branch.
  Severity: low. Likelihood: high.
  Mitigation: use `make markdownlint` for Markdown gating and stage only the
  files this change owns.

## Progress

- [ ] EP-M1 Write `docs/squash-restack-boundary-recovery.md` and
      `docs/adr-004-squash-restack-evidence-precedence.md` — the
      specification this plan implements.
- [ ] EP-M2 Build the hard fixtures first: squash-merged parent, advanced
      parent, and rewritten parent, in `tests/git_repo_helpers.py`.
- [ ] EP-M3 Pure value types, receipt and shared-record formats, gates and
      assessment, with unit, parameterized and property tests.
- [ ] EP-M4 Read-only Git query adapter and the separate ref-writing adapter,
      with the read-only guarantee and the range partition invariants.
- [ ] EP-M5 Evidence collection pipeline, report rendering, CLI, console
      script, manual page — local evidence only. Shippable plateau.
- [ ] EP-M6 Receipt recording behind `--record`, with expected-old checking
      and provenance.
- [ ] EP-M7 GitHub adapter, recorded cassette, `--json`, behavioural
      scenarios, and the remaining documentation.
- [ ] EP-M8 (separable) `git plonk` branch tombstones, so hard mode stops
      destroying the evidence this command depends on.

## Surprises & discoveries

- Observation: this repository has no roadmap document.
  Evidence: a repository-wide search of `README.md`, `.github/`, and `docs/`
  finds no roadmap file. The only matches for "roadmap" are the roadmap
  _branch-naming_ convention used by `git plonk`
  (`git_donkey/plonk_policy.py`) and a generic roadmap-authoring template in
  `docs/documentation-style-guide.md`.
  Impact: the standing instruction to mark a roadmap entry as done on
  completion has no target. The implementor should instead add the new
  documents to `docs/contents.md`, set this plan's status to `COMPLETE`, and
  escalate if a roadmap document appears before the work lands.
- Observation: `git donkey` already holds the parent identity at branch
  birth, and deliberately discards it.
  Evidence: `git_donkey/donkey_worktrees.py:184-192` resolves
  `start_point = context.repo_home.commit(request.base_branch).hexsha` and
  then passes `--no-track` to `git worktree add`, with the comment "Freeze
  the selected ref once and never track the remote default branch from a new
  feature branch".
  Impact: most of the forensic ladder exists to reconstruct a fact the tool
  family had in hand milliseconds earlier. This is the strongest argument for
  delivering receipts early (EP-M6) and for the follow-up recorded in
  `Decision log` that `git donkey` should write a birth-time receipt.
- Observation: `git plonk --hard` destroys the evidence this command needs.
  Evidence: `git_donkey/plonk.py:160` `delete_branch` deletes a completed
  local branch; Git removes the branch's reflog along with the ref.
  Impact: the highest-likelihood cause of the refusal path is another command
  in the same package. EP-M8 addresses it; until then the users' guide must
  warn that running `git plonk --hard` on a parent worktree forecloses
  fork-point recovery for its children.
- Observation: the existing vcrpy fixture proves the _absence_ of GitHub
  traffic rather than replaying traffic, and the developers' guide forbids
  hand-editing recordings.
  Evidence: `tests/integration/conftest.py:79-100` defines
  `github_api_cassette`, replaying
  `tests/integration/cassettes/github_api_no_interactions.yaml` (body:
  `interactions: []`) in VCR `none` record mode.
  `docs/developers-guide.md:557-558` states "Record a real cassette only for
  a command that is meant to call the API, and never edit a recording by
  hand."
  Impact: EP-M7 records genuine cassettes against this repository's own
  merged pull requests rather than authoring fictions, and sets
  `allow_playback_repeats=True` where one cassette serves several
  parameterized cases.
- Observation: GitHub now models stacked pull requests natively, and the
  relationship is readable through the REST API.
  Evidence: verified against the live API from this working tree on
  2026-09-14. `GET /repos/leynos/git-donkey/stacks` returns `[]` (HTTP 200,
  so the endpoint exists), and
  `GET /repos/leynos/git-donkey/pulls/80` returns a payload containing
  `"stack": null`. The feature entered public preview in July 2026 and,
  per GitHub's documentation, retargets the remaining branches automatically
  when one pull request in a stack merges; it requires all branches to be in
  the same repository.
  Impact: a natively stacked pull request is an attested statement of the
  parent relationship, so EP-M7 reads it as the highest-precedence source of
  parent _identity_. It also bounds the problem: this command is for stacks
  GitHub does not manage, and for stacks spanning forks.
- Observation: `github3.py` 4.0.1 does not model the `stack` field.
  Evidence: `github3.pulls._PullRequest._update_attributes` sets
  `merge_commit_sha`, `merged_at`, `base`, and `head`, but no `stack`;
  `github3.models.GitHubCore.as_dict()` returns `self._json_data`, the raw
  payload.
  Impact: read `stack` through `pull_request.as_dict().get("stack")`, and
  reach `GET /repos/{owner}/{repo}/stacks` through the library's
  `requests.Session` so that vcrpy still intercepts it. Record this in the
  developers' guide.
- Observation: `git patch-id --stable` is not the default.
  Evidence: `git patch-id --help` on Git 2.52.0 documents `--stable` as "the
  default if `patchid.stable` is set to true", so `--unstable` is the default
  otherwise. Both forms ignore all whitespace within the patch.
  Impact: the adapter must pass `--stable` explicitly rather than relying on
  the user's configuration, or two runs on two machines can disagree.
- Observation: `git merge-base --is-ancestor` returns 128, not 2, for an
  unknown object on this Git version.
  Evidence: measured in this working tree on Git 2.52.0 — exit `0` for an
  ancestor, `1` for a non-ancestor, `128` for a nonexistent object ID.
  Impact: the adapter must treat any status other than `0` or `1` as
  indeterminate rather than assuming a specific error code.

## Decision log

- Decision: model the three identities from the recovery procedure —
  `PARENT_HEAD`, `LANDED`, and `OLD_BASE` — as three distinct fields that are
  never interchangeable, and name the child's tip `child_tip` rather than
  `OLD_HEAD` in code.
  Rationale: the procedure's central warning is that these are different
  commits that are easy to conflate. `OLD_HEAD` and `OLD_BASE` share a prefix
  and mean unrelated things, which is exactly the adjacency this design works
  to avoid elsewhere; `child_tip` cannot be misread. `OLD_HEAD` survives in
  prose and in shell transcripts, where fidelity to the published procedure
  matters.
  Date/Author: 2026-09-14, planning agent.
- Decision: separate **parent identity** evidence from **boundary** evidence.
  Rationale: the two questions have different sources and different failure
  modes. A GitHub native stack or a `stackParent` config value tells you
  _which_ pull request the parent is; it says nothing about _which commit_
  the child forked from. Conflating them was why the first draft could not
  say who owns discovering the child's own pull request.
  Date/Author: 2026-09-14, planning agent.
- Decision: rank boundary evidence in three tiers — `ATTESTED`, `DERIVED`,
  `INFERRED` — rather than splitting it into heuristic and non-heuristic.
  Rationale: a two-way split classified `FORK_POINT` as establishing, even
  though `Risks` rates fork-point surprise as the highest-likelihood hazard
  in the design and the procedure says fork-point output must be "validated
  as a candidate", never taken as an answer. Three tiers let the rule match
  the risk: attested evidence may establish alone, derived evidence needs a
  second independent agreeing candidate, inferred evidence never establishes.
  Date/Author: 2026-09-14, planning agent.
- Decision: make the assessment result a discriminated union —
  `Established | Unresolved | Indeterminate` — where `Established.support`
  cannot hold an inferred candidate, and delete the `UNSUPPORTED` verdict.
  Rationale: the first draft promised that heuristic evidence would be
  "structurally incapable" of establishing a boundary and then implemented
  that promise as a frozen set and an `if`, guarded only by a property test —
  precisely the arrangement the same paragraph condemned. A union whose
  `Established` arm cannot hold an inferred candidate makes the illegal state
  unrepresentable, and the property test becomes a second line of defence
  instead of the only one. `UNSUPPORTED` went because nothing in the draft
  ever said when to choose it over `AMBIGUOUS`, and both mapped to the same
  exit code; the reason now lives in the `Unresolved.reasons` field that
  already existed.
  Date/Author: 2026-09-14, planning agent.
- Decision: separate the read-only Git port (`WheresatGraph`, in
  `git_donkey/wheresat_graph.py`) from the writing port
  (`WheresatRefWriter`, in `git_donkey/wheresat_refs.py`), and construct the
  writer only when the run will write.
  Rationale: bundling `fetch_evidence` with seven query methods meant every
  code path that wanted to ask an ancestry question held a fetch capability,
  and would later hold `update-ref` too. With the split, "read-only by
  default" is enforced by the writing object not existing, rather than by the
  domain of a test's argument generator.
  Date/Author: 2026-09-14, planning agent.
- Decision: compare a candidate's **cumulative** change against the squash
  commit, not its per-commit patch identities.
  Rationale: a squash is an N-to-1 relationship. The decisive comparison is
  `patch-id(diff(merge-base(candidate, trunk)..candidate)) == patch-id(S)`,
  or the cheaper tree comparison `tree(candidate) == tree(S)`. The first
  draft specified `patch_identifiers(revs) -> dict[str, str]`, a per-commit
  mapping that would only ever fire when the parent happened to be a single
  commit — it would have shipped a heuristic that silently never matched.
  This also matches git-machete's `simple` (tree) and `exact` (patch) modes.
  Date/Author: 2026-09-14, planning agent.
- Decision: use four exit codes — `0` established, `1` unresolved, `2` usage
  or environment error, `3` indeterminate — departing from the three-code
  convention used by `git incoming` and `git outgoing`.
  Rationale: the command has four genuinely different things to say, and the
  draft's three-code scheme filed "this repository cannot answer" under the
  same code as "you typed it wrong". That is the machine-readable version of
  the very conflation the "an error is not a negative answer" constraint
  exists to prevent. The departure is small, documented in the manual page,
  and the codes remain ordered by severity.
  Date/Author: 2026-09-14, planning agent.
- Decision: reach GitHub through `github3.py`, which the project already
  depends on and already authenticates in `git_donkey/fafo_github.py`, rather
  than shelling out to the `gh` CLI — but do **not** reuse
  `fafo_github._github_token()` unchanged.
  Rationale: consistency with the existing GitHub surface, and decisively,
  vcrpy intercepts in-process HTTP and cannot see traffic from a `gh`
  subprocess, so the requirement to mock GitHub with vcrpy is only
  satisfiable in-process. The token path needs factoring because
  `fafo_github._ensure_interactive` (`git_donkey/fafo_github.py:84-93`) dies
  with the `git-fafo` prefix and exit code `1` when there is no terminal,
  and `_device_flow_token` blocks in `loctocat.poll()` when there is one.
  For `git wheresat`, exit `1` means "legitimately unresolved", so that
  behaviour would be actively misleading, and a discovery command must never
  block on a browser.
  Date/Author: 2026-09-14, planning agent.
- Decision: keep the full evidence ladder from the supplied procedure, and
  record the recommendation to cut it.
  Rationale: the design review's strongest recommendation was to ship only
  receipt plus pull-request-head evidence and delete the shared record,
  fork-point, tree identity, and patch identity — three evidence kinds, three
  gates, four modules. That is a genuinely better cost profile. It is
  rejected here because the supplied recovery procedure specifies each of
  those rungs, including their limitations, and the task is to automate that
  procedure rather than a subset of it. The cost is mitigated by sequencing:
  EP-M5 ships a working local-evidence command, and the expensive rungs
  arrive in EP-M7 where a reviewer can see their price in isolation.
  Date/Author: 2026-09-14, planning agent.
- Decision: reject commit-message trailers as the boundary carrier.
  Rationale: the design review's strongest _alternative_ was to have
  `git donkey` install a `prepare-commit-msg` hook stamping
  `Stack-Parent:`/`Stack-Base:` trailers onto the child's first commit, after
  Gerrit's `Change-Id` and Jujutsu's change IDs. It is genuinely better on
  durability: trailers survive a fresh clone, a different machine, a
  fork-based pull request, parent force-push, branch deletion,
  `git plonk --hard`, and reflog expiry, and they answer the rewritten-parent
  case this command must otherwise refuse. It is rejected because the
  supplied procedure states that "No Jira issue or per-commit prefix is
  necessary", because it irreversibly mutates commit messages, and because it
  offers nothing for the branches that already exist — which is the acute
  problem. It is recorded here, and in the ADR, as the alternative to revisit
  if receipts prove insufficient in practice.
  Date/Author: 2026-09-14, planning agent.
- Decision: `git donkey` writing a birth-time receipt is a follow-up, not
  part of this plan.
  Rationale: it is the highest-value change in this whole space — it would
  make the forensic ladder apply to a shrinking population rather than to
  every invocation forever. It is excluded here because it changes an
  existing command's behaviour, needs its own tests, documentation, and
  migration note, and would push this plan past its scope tolerance before
  the forensic path exists to validate it against. Recorded as the first item
  in `Outcomes & retrospective` for the next plan.
  Date/Author: 2026-09-14, planning agent.
- Decision: keep `--json`, and record that the review recommended deferring
  it.
  Rationale: the review is right that this invents a repository-wide
  convention for a consumer that does not exist yet. It is kept because the
  task requires snapshot coverage where "multivariant output format
  consistency is relevant", because a forensic command whose answer feeds a
  scripted rebase is the clearest case in this package for machine-readable
  output, and because the agent skill in `skill/git-donkey-worktrees/` is a
  plausible first consumer. The convention is written into
  `docs/developers-guide.md` in EP-M7 so the next command inherits it rather
  than reinventing it.
  Date/Author: 2026-09-14, planning agent.
- Decision: rename `--allow-heuristics` to `--deep`, and document it as a
  cost control that can add candidates to the report but can never change the
  verdict.
  Rationale: the flag reads like a semantics switch and is not one — inferred
  evidence cannot establish a boundary with or without it. What it actually
  controls is whether a potentially expensive scan of the trunk runs at all.
  Naming it for what it does removes a false affordance.
  Date/Author: 2026-09-14, planning agent.
- Decision: record `git wheresat` in `git_donkey/observability.py` rather
  than leaving it silent.
  Rationale: every other command in the package feeds the bounded recorder,
  and this is the command most likely to be wrong in a way nobody notices.
  Recording which evidence tier established a boundary, across a fleet, is
  the single most useful signal for knowing whether the command is
  trustworthy. The vocabulary additions are enumerated in
  `Interfaces and dependencies` and budgeted; anything beyond them trips a
  tolerance.
  Date/Author: 2026-09-14, planning agent.
- Decision: write `docs/squash-restack-boundary-recovery.md` and the ADR
  **first**, in EP-M1, before any types exist.
  Rationale: the first draft scheduled them four milestones after the code
  that implements them, by which point the evidence vocabulary, the gate
  names, and the verdict model would already be frozen. An architectural
  decision record written after the types it justifies is a rationalization,
  and the ambiguity tolerance cannot fire against a document that has not
  been written.
  Date/Author: 2026-09-14, planning agent.

## Outcomes & retrospective

To be completed at each milestone boundary and at completion. Before setting
the status to `COMPLETE`, reconcile every implementation discovery against
`docs/squash-restack-boundary-recovery.md` and
`docs/adr-004-squash-restack-evidence-precedence.md`: a discovery that
falsifies a stated assumption requires updating that document and re-checking
every trace link in `Conformance basis`, not a quiet amendment here.

Follow-up work this plan deliberately leaves undone, in priority order:

1. Have `git donkey` write a birth-time receipt when it creates a stacked
   branch. It already freezes the start point at
   `git_donkey/donkey_worktrees.py:186`; writing `refs/stack-bases/<branch>`
   and `branch.<branch>.stackParent` there would make the forensic ladder a
   legacy path.
2. EP-M8, if it was not delivered with this plan: `git plonk` branch
   tombstones.
3. Revisit commit-message trailers if receipts prove insufficient across
   clones, and revisit pushing `refs/stack-bases/<branch>` to the remote as a
   cheaper cross-clone mechanism than the shared record.

## Context and orientation

Assume no prior knowledge of this repository.

`git-donkey` is a Python package that installs a family of Git subcommands.
Git resolves `git <name>` by looking for an executable called `git-<name>` on
`PATH`, so each subcommand is a console script declared in `pyproject.toml`
under `[project.scripts]`. There is no shim generator. The existing scripts
are `git-donkey`, `git-track`, `git-fafo`, `git-plonk`,
`git-donkey-template`, `git-incoming`, `git-in`, `git-outgoing`, and
`git-out`, each pointing at a function in `git_donkey/cli.py`.

`git_donkey/cli.py` is the console-script boundary and owns every
[Cyclopts](https://cyclopts.readthedocs.io/) `App`. The idiom, visible at
`git_donkey/cli.py:266-294` for `git plonk`: construct `App(name=...,
help=...)` at module level, decorate one function with `@app.default`, and
have that function do nothing but call a `run_git_*` function in a workflow
module and `raise SystemExit(<returned int>)`. A plain
`def git_<name>() -> None: _app()` function is the console-script target.

Each command is decomposed into several modules, because
`docs/developers-guide.md:608-610` records that the split "runs along the
production boundaries each module verifies and keeps every module below
CodeScene's Low Cohesion threshold of four". `git plonk` is the exemplar,
split into `git_donkey/plonk.py` (orchestration plus Git adapters),
`git_donkey/plonk_policy.py` (pure policy), `git_donkey/plonk_records.py`
(shared value types), `git_donkey/plonk_selection.py` (pure selection), and
`git_donkey/plonk_summary.py` (pure rendering) — 1,122 lines in total.
`git incoming`/`git outgoing` follows the same shape with
`git_donkey/incoming_outgoing.py` and
`git_donkey/incoming_outgoing_policy.py`.

The pure modules state their rule in the module docstring — for example
`git_donkey/plonk_policy.py:3` says "This module contains no GitPython,
filesystem, or process mutation." Impure work sits behind a small
`typing.Protocol` naming exactly the Git surface required, with one frozen
dataclass implementing it over GitPython. `_ComparisonAdapter` and
`_GitPythonComparison` in `git_donkey/incoming_outgoing.py:139-186` are the
closest precedent for the adapters this plan needs.

Shared Git helpers live in `git_donkey/helpers.py`: `_find_repo(prefix)`
locates the repository from the working directory, `_fetch_remote(repo,
remote, prefix)` fetches a remote, `_first_remote_name(repo, prefix)` returns
the principal remote (by convention, `repo.remotes[0]`), `_ref_exists(repo,
ref)` checks a ref with `git show-ref --verify --quiet`, and `_die(prefix,
msg, code=2, *, emoji=None)` prints to standard error and raises
`SystemExit`. There is no custom exception hierarchy; `_die` is the
convention. Trunk discovery lives in `git_donkey/remote_default.py`:
`principal_remote(repo, prefix)`, `discover_default_branch(repo, remote,
prefix)` — which queries `git ls-remote --symref <remote> HEAD` rather than
trusting the possibly stale local `refs/remotes/<remote>/HEAD` — and
`fetch_default_branch_ref(repo, remote, branch, prefix)`.

There is **no existing helper** anywhere in `git_donkey/` for `git
merge-base`, `git for-each-ref`, reading `git config`, or reading a reflog.
This command introduces the first of each.

`git_donkey/observability.py` defines a closed vocabulary of bounded
workflow records: `type Operation = typ.Literal[...]`, `type Outcome =
typ.Literal[...]`, and label types such as `ErrorKind`. Every other command
feeds it, `tests/observability_helpers.py` holds the recording recorder, and
the root `conftest.py` installs it through the `recording_recorder` fixture.
`docs/developers-guide.md:343-372` documents the convention.

GitHub access already exists in `git_donkey/fafo_github.py`. It uses
`github3.py` (`github3.login(token=...)`) with a token taken from
`GITHUB_TOKEN` or `GH_TOKEN`, then a cached credentials file, then an
interactive OAuth device flow through `loctocat`. Two behaviours in that
module must **not** be inherited by this command:
`_ensure_interactive` (`git_donkey/fafo_github.py:84-93`) calls
`helpers._die(_GIT_FAFO_PREFIX, ..., 1)` when there is no terminal, and
`_device_flow_token` (`:96-121`) blocks in `loctocat.poll()` when there is
one. The `gh` CLI is not invoked anywhere in this project, and only
single-object GitHub calls are made today; no paginated endpoint is used yet.

Output is plain `print()` to standard output and `helpers._eprint()` to
standard error. There is no `rich` dependency and **no machine-readable
output mode anywhere in the CLI surface today**; `--json` is new ground.

Tests live in two trees. `tests/unit/` holds fast tests over pure modules,
CLI parsing, and repository contracts. `tests/integration/` holds end-to-end
tests that build real temporary repositories, with pytest-bdd feature files
in `tests/integration/features/`. Binding is `scenarios("features/<file>")`
at the bottom of a `test_*_bdd.py` module; steps live in that module and use
`target_fixture=` to thread a scenario dataclass through Given, When, and
Then. Steps shared between two BDD modules are hoisted into
`tests/integration/conftest.py`, where — because the Ruff `assert` exemption
in `pyproject.toml:123-128` covers only `**/test_*.py` and `tests/steps/*.py`
— they must use `pytest.fail(...)` rather than a bare `assert`.

Repository builders are `tests/git_repo_helpers.py::seed_repo`,
`::configure_repo`, `::repo_with_remote_default`, and
`tests/integration/conftest.py::_setup_repo`, which creates a bare remote
plus a clone with a seeded `main`.

Three test libraries matter. `syrupy` stores snapshots as
`__snapshots__/<module>.ambr`; the `ambrleaks` step in `make lint` fails the
build if a snapshot contains an absolute path, and
`tests/unit/test_plonk.py:44-50` shows the `syrupy.matchers.path_type`
redaction convention. `hypothesis` 6.168.0 runs with library defaults —
`max_examples=100`, `deadline=200ms` — because no profile is registered, and
`[tool.pytest.ini_options] timeout = 30` applies to every test while
`make test` runs `uv run pytest -v -n auto`. `vcrpy` 7.0.0 is used in
`tests/integration/conftest.py:79-100`; `vcr.cassette.Cassette` accepts
`allow_playback_repeats`, which matters when one cassette serves several
parameterized cases.

### Terms used in this plan

Taken from `docs/squash-restack-boundary-recovery.md`, which EP-M1 writes.

- **Child**: the upper branch in the stack, the one being repaired. Its
  current tip is `child_tip` (the procedure calls it `OLD_HEAD`).
- **Parent**: the lower branch, whose pull request was squash-merged.
- **`PARENT_HEAD`**: the historical head commit of the parent branch — the
  commit labelled `B` in the diagram below.
- **`LANDED`**: the parent's integration commit on the trunk, which for a
  squash merge is the new squash commit `S`, not `B`.
- **`OLD_BASE`**: the exclusive replay boundary. For the graph below it is
  `B`. It is not `C` (the first child commit), not `S`, and not `M`.
- **`TARGET`**: the commit the child is to be replayed onto, captured
  immediately as an immutable object ID.
- **Receipt**: a locally recorded boundary — the ref
  `refs/stack-bases/<branch>` plus the config keys
  `branch.<branch>.stackParent`, `.stackBaseRecordedFrom`, and
  `.stackBaseEvidence`.
- **Shared record**: the same information written in prose into a pull
  request body, for recovery from another clone.
- **Evidence tier**: how much weight a candidate boundary carries.
  `ATTESTED` may establish alone; `DERIVED` needs a second, independently
  obtained candidate naming the same commit; `INFERRED` never establishes.

```plaintext
        A---B---C---D       child
       /
M-----S-----T               target
```

`S` incorporates the parent's `A+B` changes. The intended child series is
`C,D`, so the repair is `git rebase --onto T B child`.

## Conformance basis

There is no Terms of Reference document and no roadmap document in this
repository. The upstream artefact for this work is the boundary-recovery
procedure supplied with the task, which EP-M1 commits as
`docs/squash-restack-boundary-recovery.md` so that later readers have the
same source. Requirement identifiers below refer to sections of that
document.

Governing repository standards: `AGENTS.md` (code style, test-first
delivery, quality gates, commit discipline),
`docs/documentation-style-guide.md` (prose, headings, ADR format, Mermaid),
`docs/developers-guide.md` (module boundaries, test infrastructure, cassette
policy, observability, manual pages), `docs/manpages-design.md` (manual-page
build contract), and `docs/adr-003-python-lint-architecture.md` (lint
tiering).

New upstream artefacts this plan creates, in EP-M1:

- `docs/squash-restack-boundary-recovery.md` — the design document: the graph
  and the three identities, the evidence model and its tiers, the eight gates
  with their decision procedures, the receipt and shared-record formats, the
  degraded-mode table, the limits of fork-point and of content comparison,
  and a `## Verification contract` section naming the tests that pin each
  rule.
- `docs/adr-004-squash-restack-evidence-precedence.md` — the decision record:
  why evidence is tiered rather than merged, why inferred evidence can never
  establish a boundary, why a lone derived candidate cannot either, why
  refusal is an outcome rather than an error, why four exit codes, and why
  commit-message trailers were rejected. The design document references it
  rather than restating it.

Trace links:

```plaintext
REQ-identities    -> DES-evidence-model -> EP-M3 -> test_wheresat_policy.py::test_identities_never_conflated
REQ-receipt-read  -> DES-receipt        -> EP-M3 -> test_wheresat_receipts.py::test_receipt_round_trip
REQ-receipt-write -> DES-receipt        -> EP-M6 -> test_wheresat_receipt_write.py::test_expected_old
REQ-parent-pr     -> DES-github-adapter -> EP-M7 -> test_wheresat_github.py::test_parent_metadata_contract
REQ-pr-head       -> DES-evidence-model -> EP-M7 -> git_wheresat.feature::"Established by pull request head"
REQ-fork-point    -> DES-evidence-model -> EP-M5 -> test_wheresat_policy.py::test_derived_needs_corroboration
REQ-integration   -> DES-gates          -> EP-M3 -> test_wheresat_policy.py::test_landed_must_reach_target
REQ-patch-caveat  -> DES-gates          -> EP-M3 -> test_wheresat_properties.py::test_inferred_never_establishes
REQ-read-only     -> DES-safety         -> EP-M4 -> test_wheresat_read_only.py::test_repository_unchanged
REQ-refusal       -> DES-gates          -> EP-M5 -> git_wheresat.feature::"Refusal after a rewritten parent"
```

## Verification plan

The interesting correctness of this command is concentrated in one pure
function — the assessment that turns collected evidence into a result — and
in two cross-cutting safety properties. Everything else is ordinary
integration work. The implementation structure is chosen to put those
obligations where they can be checked cheaply: the assessment is a pure
function over frozen value types with no Git or network access, every
mutating capability is confined to one object that is not constructed on the
default path, and the graph questions the assessment depends on are supplied
as a closed data record rather than as a live adapter handle.

### Non-trivial axioms

These are assumed, not verified. Each is a documented external interface.
Where a claim was measured in this working tree, the measurement is recorded.

- AXIOM-1: `git merge-base --is-ancestor A B` exits `0` when `A` is an
  ancestor of `B` and `1` when it is not. Any other status means the question
  could not be answered. Measured on Git 2.52.0 in this worktree: `0`, `1`,
  and `128` for a nonexistent object ID. The adapter must therefore treat
  "not 0 and not 1" as indeterminate rather than matching a specific code.
  Source: [git-merge-base](https://git-scm.com/docs/git-merge-base).
- AXIOM-2: `git merge-base --all` lists every best common ancestor, and
  `--fork-point` consults the reflog of the named ref and can therefore
  return nothing, or a different answer, once that reflog expires or the ref
  is deleted.
  Source: [git-merge-base](https://git-scm.com/docs/git-merge-base).
- AXIOM-3: `git patch-id` ignores all whitespace within a patch, so distinct
  changes can share one identifier. `--stable` is **not** the default; it
  applies only when `patchid.stable` is set to true, so the adapter passes it
  explicitly to make results comparable across machines. Verified against
  `git patch-id --help` on Git 2.52.0.
  Source: [git-patch-id](https://git-scm.com/docs/git-patch-id).
- AXIOM-4: `git update-ref` with an expected-old object ID fails rather than
  overwriting when the current value differs, and an empty expected-old value
  means "must not already exist".
  Source: [git-update-ref](https://git-scm.com/docs/git-update-ref).
- AXIOM-5: `git fetch --no-write-fetch-head` does not update `FETCH_HEAD`,
  and a refspec with an explicit destination writes only that destination.
  Source: [git-fetch](https://git-scm.com/docs/git-fetch).
- AXIOM-6: for a merged pull request, `merge_commit_sha` names the commit
  created on the base branch — the squash commit for a squash merge — and
  `merged_at` is non-null only for a merged pull request. Before merge, the
  same field may name a synthetic test-merge commit. A single-parent
  integration commit alone does not distinguish a squash merge from a rebase
  merge.
  Source: [GitHub REST: pulls](https://docs.github.com/en/rest/pulls/pulls).
- AXIOM-7: `refs/pull/<n>/head` names the pull request's head commit and
  `refs/pull/<n>/merge` names a synthetic merge; the head ref survives branch
  deletion but does not preserve superseded force-pushed incarnations.
  Source: [Checking out pull requests
  locally](https://docs.github.com/en/pull-requests/how-tos/review-pull-requests/checking-out-pull-requests-locally).
- AXIOM-8: `github3.py` 4.0.1 maps the pull request payload faithfully for
  the fields it models, and `GitHubCore.as_dict()` returns the raw payload so
  unmodelled fields such as `stack` remain reachable. Library internals are
  not verified; EP-M7 instead verifies the repository-owned mapping against a
  recorded cassette carrying every field the code reads.
- AXIOM-9: a force-pushed pull request head may be unreachable on the server,
  and `git gc` may prune a local object that no ref reaches. Absence of a
  historical incarnation is not evidence that it never existed.
- AXIOM-10: GitHub's stacked pull requests expose the relationship through
  `GET /repos/{owner}/{repo}/stacks` and a `stack` field on the pull request
  payload, require all branches to be in one repository, and retarget the
  remaining branches automatically when one pull request in the stack merges.
  The endpoint and the field were verified live from this worktree on
  2026-09-14; the response shape for a **non-empty** stack was not, and
  EP-M7 must confirm it against a real stacked pull request before relying on
  any field beyond its presence.
  Source: [About stacked pull
  requests](https://docs.github.com/en/pull-requests/get-started/about-stacked-prs)
  and [REST API endpoints for stacked pull
  requests](https://docs.github.com/en/rest/pulls/stacks).

### Gate semantics

A gate is a named question with a specified decision procedure and three
possible outcomes. Every gate returns `INDETERMINATE` rather than `FAILED`
when its inputs are unavailable. `C` denotes the candidate boundary.

1. `parent-identity-matches` — the parent pull request used is the one
   requested or derived, and the head ref was fetched from the repository
   named by the pull request's own `head.repo.full_name`. `FAILED` when
   either differs. `INDETERMINATE` when no parent identity is known.
2. `parent-merged` — the pull request reports `merged` true and a non-null
   `merged_at`. `FAILED` when it is open or closed-unmerged.
3. `landed-reachable-from-target` — `git merge-base --is-ancestor LANDED
   TARGET` returns ancestor. `FAILED` when it returns non-ancestor.
4. `boundary-is-ancestor-of-child` — `git merge-base --is-ancestor C
   child_tip` returns ancestor. `FAILED` when it returns non-ancestor.
5. `replay-range-non-empty` — `git rev-list --count C..child_tip` is greater
   than zero. `FAILED` at zero, which means the child has no work to replay.
6. `parent-history-intact` — `PARENT_HEAD` is known, its object is present,
   and `git merge-base --is-ancestor C PARENT_HEAD` returns ancestor, so the
   candidate lies on the parent's own history rather than on the trunk.
   `FAILED` otherwise. This is the gate that catches the rewritten-parent
   case, where `git merge-base --all` returns an earlier trunk commit.
   `INDETERMINATE` when `PARENT_HEAD` cannot be recovered.
7. `replay-range-excludes-landed-work` — no commit in `C..child_tip` is
   reachable from `PARENT_HEAD`, and the cumulative patch identifier of the
   range does not equal the patch identifier of `LANDED`. Computed as
   `git rev-list C..child_tip --not PARENT_HEAD` compared against
   `git rev-list C..child_tip`, plus one
   `git diff C child_tip | git patch-id --stable` comparison. `FAILED` when
   either check finds landed work inside the proposed replay range.
   `INDETERMINATE` when `PARENT_HEAD` or `LANDED` is unknown. This gate is
   named for what it can actually check: it cannot prove the suffix is
   _only_ child work, and AXIOM-3 means its patch comparison is not
   injective. That limitation is why it is one gate among eight rather than
   the whole answer.
8. `receipt-not-superseded` — applies only to a receipt candidate. The
   recorded child tip (`branch.<b>.stackBaseRecordedFrom`) is still an
   ancestor of the current child tip, and no parent integration newer than
   the receipt is visible. `FAILED` demotes the receipt from `ATTESTED` to
   `DERIVED` and records the reason; it does not by itself refuse.

An `Established` result requires every applicable gate to return `PASSED`.
Gates 1, 2, 3, 6, and 7 are not applicable when the evidence never needed a
parent pull request, in which case they are recorded as `INDETERMINATE` and
the result cannot be `Established` — which is why the local-evidence-only
command delivered at EP-M5 reports indeterminate for anything it cannot
confirm, rather than guessing.

### Invariants and lemmas

**INV-1 — read-only by construction.**
Obligation: a `git wheresat` invocation without `--record` leaves `HEAD`, the
index, the working tree, `FETCH_HEAD`, all branches, all tags, all
remote-tracking refs, all stash entries, and all local configuration
byte-identical. The only permitted difference is the creation of refs under
`refs/wheresat/`.
Method: parameterized test over an explicit matrix of argument vectors,
executed against a real temporary repository.
Rationale: this is the command's headline promise. It spans the whole
process, so it needs a real repository. It is a parameterized matrix rather
than a Hypothesis property because the domain is a finite set of flag
combinations and because each example costs roughly 150-250 ms — straddling
Hypothesis's 200 ms default deadline under a 30-second pytest timeout with
`-n auto`, which would make the first genuine counterexample arrive as a
timeout during shrinking rather than as a minimal failing case.
Domain: at least eight vectors covering the default, `--no-fetch`,
`--offline`, `--deep`, `--json`, `--branch` with a valid and an invalid
value, `--onto` with an unresolvable revision, and `--op-id` with hostile
values (`../`, a leading `-`, an embedded `:`, an embedded newline).
Artefact: `tests/integration/test_wheresat_read_only.py`, marked
`@pytest.mark.timeout(120)`.
Evidence: a snapshot of `git for-each-ref
--format='%(refname) %(objectname)'`, `git status --porcelain=v2 --branch`,
`git stash list`, `git config --local --list`, and a digest of every tracked
file, compared before and after. Before implementation the test fails because
the module does not exist.
Non-vacuity: assert that at least one vector reached the fetch path and at
least one reached an error path, by inspecting the recorded observations. The
negative control is a deliberately mutating writer, injected in a companion
test, which must make the comparison fail — proving the snapshot is
sensitive.

**INV-2 — inferred evidence never establishes a boundary.**
Obligation: `Established.support` cannot contain an `InferredCandidate`, and
`assess` never returns `Established` when every candidate is inferred.
Method: the type system first — `Established.support` is typed
`tuple[AttestedCandidate | DerivedCandidate, ...]`, so Pyright and `ty`
reject the illegal construction — plus a Hypothesis property test over
generated candidate sets.
Rationale: the procedure is explicit that patch and tree comparisons are
forensic evidence rather than an oracle. Making the illegal state
unrepresentable means a regression is a type error; the property test then
guards the selection logic that chooses what goes into `support`.
Domain: sets of one to thirty-two candidates with generated tiers, commit
identifiers, and supporting and contradicting statements. Thirty-two is the
production cap on the candidate set, so the tested domain matches the real
one.
Artefact: `tests/unit/test_wheresat_properties.py`.
Evidence: `uv run pytest tests/unit/test_wheresat_properties.py -q`. Fails
before implementation because `assess` does not exist.
Non-vacuity: classify generated cases into all-inferred, mixed, and
no-inferred, and require each class to occur. The negative control is a
mutant `assess` returning `Established` with `candidates[0]` whenever the
list is non-empty; the property must reject it.

**INV-2b — a lone derived candidate never establishes a boundary.**
Obligation: `assess` returns `Established` only when `support` contains at
least one `AttestedCandidate`, or at least two candidates from independent
sources naming the same commit.
Method: Hypothesis property test, same domain as INV-2.
Rationale: `Risks` rates fork-point surprise as the highest-likelihood hazard
in this design, and AXIOM-2 says fork-point answers change as reflogs expire.
A binary heuristic/non-heuristic split would have let a lone fork-point
candidate establish a boundary, aiming the safety net at the wrong risk.
Domain: as INV-2, with source identity generated so "independent" is
checkable.
Artefact: `tests/unit/test_wheresat_properties.py`.
Evidence: as INV-2.
Non-vacuity: require generated cases with exactly one derived candidate, with
two agreeing derived candidates from the same source, and with two agreeing
derived candidates from different sources; the first two must not establish
and the third must be able to. The negative control is a mutant that counts
two candidates from one source as corroboration.

**INV-3 — order independence.**
Obligation: `assess` returns the same result for any permutation of its
candidate sequence, apart from the ordering of the reported candidate list.
Method: Hypothesis property test.
Rationale: "do not choose the first result" is only enforceable if the answer
cannot depend on position. Permutation invariance is the exact formal
statement of that requirement.
Domain: as INV-2, with a generated permutation applied.
Artefact: `tests/unit/test_wheresat_properties.py`.
Evidence: as INV-2.
Non-vacuity: require generated cases with at least two distinct candidates.
The negative control is the same `candidates[0]` mutant, which must fail for
some permutation.

**INV-4 — the result is the conjunction of the applicable gates.**
Obligation: the result is `Established` if and only if every applicable gate
returned `PASSED`; any `FAILED` gate yields `Unresolved`; any
`INDETERMINATE` gate yields `Indeterminate` and no claim about ancestry.
Method: parameterized test over the explicit truth table for the eight named
gates, plus a Hypothesis property over generated gate vectors.
Rationale: the gate set is finite and each row has distinct meaning, so
enumeration is readable; the property guards against a ninth gate being added
without being wired into the conjunction.
Domain: the truth table, and generated vectors over `GateOutcome`.
Artefact: `tests/unit/test_wheresat_policy.py` and
`tests/unit/test_wheresat_properties.py`.
Evidence: both suites fail before `assess` exists.
Non-vacuity: the table must include one all-passed row — a witness that
`Established` is reachable at all — and one row per gate in which that gate
alone fails. Additionally, **each gate must have at least one test against a
real repository or a recorded cassette in which that gate alone returns
`FAILED`**, so a gate implemented as an unconditional `PASSED` is caught. The
negative control is a mutant that ignores one named gate; the isolating row
and the isolating repository test must both fail.

**INV-5 — an error is not a negative answer.**
Obligation: when the Git adapter or the GitHub adapter raises, the
corresponding gate outcome is `INDETERMINATE`, the result is `Indeterminate`,
the exit code is `3`, and the run does not fall through to a lower evidence
tier. The report never states that the boundary is not an ancestor.
Method: parameterized test with faulting adapter stubs, plus one behavioural
scenario over a genuinely shallow clone.
Rationale: conflating "cannot tell" with "no" is the specific failure the
procedure warns about, and it is invisible in happy-path testing. The GitHub
side is the more dangerous one: a 404 from a token that has lost `repo` scope
on a private repository is indistinguishable from "that pull request does not
exist", and reading it as the latter lets a credentials problem become a
confident wrong answer.
Domain: each `WheresatGraph` method faulted in turn; and for
`WheresatGitHub`, one case each for HTTP 401, 403 rate-limited with
`Retry-After`, 403 forbidden, 404, 500, a connection timeout, and a DNS
failure.
Artefact: `tests/unit/test_wheresat_policy.py`,
`tests/unit/test_wheresat_github_faults.py`, and
`tests/integration/features/git_wheresat.feature`.
Evidence: the scenario "Shallow history cannot answer the ancestry
question", and for the rate-limit case a recorded cassette carrying a 403
body with `Retry-After` and `X-RateLimit-Reset`.
Non-vacuity: assert the exit code is `3`, that the rendered report does not
contain the "not an ancestor" phrasing, and that no lower-tier evidence was
collected after the fault — so a mutant that returns the right code with the
wrong message, or that quietly continues, still fails.

**INV-6 — the boundary partitions the child history.**
Obligation: for an `Established` result, the reported included commits are
exactly those reachable from `child_tip` and not from `OLD_BASE`; included
and excluded are disjoint; and their union is the set reachable from
`child_tip`.
Method: two complementary artefacts. A Hypothesis property over generated
`GraphFacts` **data** — synthetic directed acyclic graphs, no repository
built — and a parameterized test over six real repository shapes.
Rationale: the property ranges over graph shape, so generation beats
enumeration; but building a real repository per generated example is too
expensive for Hypothesis's defaults, so the generated half operates on data
and the real half is a finite, readable matrix.
Domain: generated graphs of up to twenty nodes; and the shapes linear,
forked-then-linear, advanced parent, rewritten parent, empty replay range,
and a criss-cross merge producing multiple merge bases.
Artefact: `tests/unit/test_wheresat_properties.py` and
`tests/integration/test_wheresat_ranges.py`.
Evidence: compare against `git rev-list OLD_BASE..child_tip` computed
independently.
Non-vacuity: classify generated graphs so linear and forked shapes both
occur, and require at least one case with a non-empty excluded set. A mutant
using an inclusive range must be rejected.

**INV-7 — receipt writes are create-only unless an expected old value is
supplied.**
Obligation: `--record` creates `refs/stack-bases/<branch>` only when it does
not exist; when it does exist, the write fails unless `--expected-old`
matches the current value exactly. A receipt is written only when the result
was `Established` from attested evidence.
Method: parameterized tests over the existing, expected, and new triple, plus
a property that no `(existing, expected)` pair with `existing != expected`
ever changes the ref.
Rationale: the procedure requires an existing receipt be reviewed and updated
with an expected-old object ID, never silently overwritten.
Domain: ref absent; present with matching expectation; present with
mismatched expectation; present with no expectation; and a run whose result
was `Unresolved`.
Artefact: `tests/integration/test_wheresat_receipt_write.py`.
Evidence: the ref value after each case, read with `git rev-parse`.
Non-vacuity: include the case that legitimately updates the ref, so the test
distinguishes "always refuses" from "refuses correctly". The negative control
is a mutant that omits the expected-old argument.

**INV-8 — a reported boundary is durable.**
Obligation: when the result is `Established`, the boundary commit is
reachable from at least one ref that survives the process. If it is reachable
only through a per-run evidence ref, the run retains
`refs/wheresat/boundary/<branch>` and the report names it.
Method: parameterized test over two cases — boundary already reachable from a
branch, and boundary reachable only from the fetched evidence ref.
Rationale: without this, a successful run can hand the user an answer that
`git gc --prune=now` invalidates minutes later, turning a correct boundary
into `fatal: invalid upstream`. The evidence refs are not scratch; they are
load-bearing for the answer's continued existence.
Domain: the two cases above, each followed by deleting every per-run evidence
namespace and running `git gc --prune=now`.
Artefact: `tests/integration/test_wheresat_durability.py`, marked
`@pytest.mark.timeout(120)`.
Evidence: `git rev-parse --verify <boundary>^{commit}` still succeeds after
the garbage collection, and the report names the retained ref in the second
case.
Non-vacuity: the first case must genuinely not retain a ref, so the test can
tell "always retains" from "retains when needed". The negative control is a
mutant that never retains, which the second case must reject.

**LEM-1 — pull request head ancestry supports the boundary.**
Statement: if the fetched `PARENT_HEAD` is an ancestor of `child_tip`, and
gates 1, 2, 3, 5, 6, and 7 all pass, then `OLD_BASE = PARENT_HEAD` is sound.
This is the common case named in the procedure. It is a lemma rather than an
axiom because it depends on those gates holding; it is discharged by INV-4's
truth table together with the behavioural scenario for that path. The
residual gap is explicit: ancestry alone does not prove the suffix contains
only child work, which is why gate 7 is separate and why gate 7's own
limitation is stated in `Gate semantics` rather than hidden.

**LEM-2 — a unique merge base is a candidate, not a conclusion.**
Statement: when the parent advanced linearly after the child forked, a unique
result from `git merge-base --all PARENT_HEAD child_tip` may be the inherited
boundary, but only with `parent-history-intact` passing and a second
independent candidate agreeing.
Discharged by gate 6, by the `DERIVED`-tier corroboration rule (INV-2b), and
by the behavioural scenario "Refusal after a rewritten parent", which
exercises the case where the unique merge base is an earlier trunk commit and
must be refused. Residual gap: the intactness check relies on surviving refs
and reflogs; when those are gone the command refuses, which is the intended
behaviour rather than a verification hole. `git plonk --hard` is the most
likely cause of them being gone, which is why EP-M8 exists.

### Residual gaps

No formal proof or model check is proposed. The state space is small and
finite, the properties above are total over their generated domains, and the
remaining risk is concentrated in external-interface assumptions that a proof
could not discharge. Two gaps are accepted explicitly: gate 7 cannot prove
the replay range is _only_ child work, and AXIOM-3 means content comparison
is not injective. Both are why the command refuses rather than guesses when
they are the only evidence left. If a future change introduces concurrent
evidence collection within one process, or a retry protocol, that is the
point to reconsider a state-machine model.

## Plan of work

The work proceeds specification first, then hardest fixture first, then
inside out: pure value types and assessment, then the two Git ports, then
collection, rendering, and the command line for local evidence only, then
receipts, then GitHub. Each stage ends with validation, and no stage begins
until the previous stage's validation passes.

Red-Green-Refactor applies **within each milestone**, not across the whole
plan. Author a milestone's tests at that milestone's start, watch them fail
for the intended reason, make them pass, then clean up. Do not attempt to
write every test in the plan before any production code exists; a week spent
entirely red is how test suites get deleted. Strict expected-failure markers
are not used: the red stage is proven by running the test and recording the
failure, and `Quality criteria` forbids an expected failure surviving into
the finished work anyway.

The flow the finished command implements is shown below. The diagram reads
top to bottom: inputs are resolved, the parent's identity is established from
the strongest available source, boundary candidates are collected in tier
order, every candidate passes through the same gate set, and only a candidate
whose evidence tier permits establishment and which clears every applicable
gate produces a proposed rebase command.

```mermaid
flowchart TD
    resolve["Resolve child, target, and options"]
    ident["Identify the parent: explicit, receipt,<br/>GitHub stack, shared record, association search"]
    attested["Collect ATTESTED candidates:<br/>receipt ref, shared-record object ID, pull request head"]
    derived["Collect DERIVED candidates:<br/>merge-base --all, merge-base --fork-point"]
    deep{"--deep requested?"}
    inferred["Collect INFERRED candidates:<br/>tree identity, then cumulative patch identity"]
    gates["Apply every applicable gate to every candidate"]
    tier{"An ATTESTED candidate, or two agreeing<br/>independent candidates, clears all gates?"}
    indet{"Any gate indeterminate?"}
    ok["Report boundary, gates, backup ref,<br/>and the rebase --onto command; exit 0"]
    stop["Report candidates, gates, and the<br/>unresolved distinction; exit 1"]
    unknown["Report what could not be determined; exit 3"]

    resolve --> ident
    ident --> attested
    attested --> derived
    derived --> deep
    deep -- yes --> inferred
    deep -- no --> gates
    inferred --> gates
    gates --> tier
    tier -- yes --> ok
    tier -- no --> indet
    indet -- yes --> unknown
    indet -- no --> stop
```

_Figure 1: parent identification, tiered evidence collection, and gate
evaluation in `git wheresat`._

**Stage A — specify.** Write the design document and the ADR. No code. This
stage ends when the eight gates, the three evidence tiers, the four exit
codes, and the receipt and shared-record formats are written down and
internally consistent.

**Stage B — build the hard fixtures.** Extend `tests/git_repo_helpers.py`
with the three scenario builders the whole plan rests on, and prove each
builds the history it claims. The rewritten-parent fixture comes first,
because it is the case the refusal path exists for and the one with no
existing precedent in this repository.

**Stage C — pure core.** Value types, receipt and shared-record formats,
gates, assessment. Tests first, milestone by milestone.

**Stage D — ports and command.** The read-only graph port, the separate
writing port, collection, rendering, and the command line. This stage ends
with a working command that answers from local evidence and says so honestly
when it cannot.

**Stage E — receipts and GitHub.** Receipt recording, then the GitHub
adapter, `--json`, the behavioural suite, and the remaining documentation.

**Stage F — refactor and validate widely.** Split any module approaching the
800-line Pylint cap or the CodeScene cohesion threshold, run `cs check` on
each new file and `cs delta origin/main`, and run the full gate set.

## Milestones and plateaus

Each milestone ends in a repository state that is correct and internally
coherent, and safe to stop at. No milestone introduces a compatibility shim:
every new interface is private to this package, introduced after the latest
release tag, and has no external consumer, so interfaces and their callers
change together. Every milestone's compatibility decision is therefore
"none required", and the reason is the same each time; it is not repeated
below.

**EP-M1 — the specification.**
Outcome: `docs/squash-restack-boundary-recovery.md` and
`docs/adr-004-squash-restack-evidence-precedence.md` exist and are linked
from `docs/contents.md`. No code.
Requirements: establishes the `REQ-*` identifiers the rest of the plan
traces to.
Acceptance evidence: `make markdownlint` and `make nixie` pass; the design
document's `## Verification contract` section names every test this plan
will create; the ADR follows the template at
`docs/documentation-style-guide.md:267-316`.
Conformance check: the eight gates, three tiers, and four exit codes in the
documents match this plan exactly. Any divergence is resolved here, not
later.
Recovery: documentation only; revert the commit.
Remaining gaps: everything else.

**EP-M2 — the hard fixtures.**
Outcome: `tests/git_repo_helpers.py` gains `squash_merged_stack()`,
`advanced_parent_stack()`, and `rewritten_parent_stack()`, each returning a
record naming `child_tip`, `parent_head`, `landed`, `target`, and the
`expected_old_base` (or `None` where the boundary is genuinely
unrecoverable). `tests/unit/test_git_repo_helpers.py` proves each builder
produces the ancestry it claims.
Requirements: de-risks REQ-refusal and REQ-fork-point.
Acceptance evidence: for `rewritten_parent_stack()`,
`git merge-base --all parent_head child_tip` returns a commit that is **not**
an ancestor of `parent_head` — that is the fixture's whole point, and the
assertion proving it is the milestone.
Conformance check: builders live with the existing shared builders and
configure a local commit identity, so tests never read the runner's global
Git configuration.
Recovery: test-only; revert.
Remaining gaps: no production code.

**EP-M3 — pure value types, formats, gates, and assessment.**
Outcome: `git_donkey/wheresat_records.py`,
`git_donkey/wheresat_receipts.py`, and `git_donkey/wheresat_policy.py`
exist, with no Git, filesystem, network, or process access. The unit,
parameterized, and property suites for INV-2, INV-2b, INV-3, INV-4, INV-6's
generated half, and the format round-trips pass.
Requirements: REQ-identities, REQ-receipt-read, REQ-patch-caveat,
REQ-integration.
Acceptance evidence: `uv run pytest tests/unit/test_wheresat_policy.py
tests/unit/test_wheresat_receipts.py
tests/unit/test_wheresat_properties.py -q` passes, each suite having failed
first. `uv run ty check` rejects a deliberately added
`Established(support=(InferredCandidate(...),))` — the type-level half of
INV-2.
Conformance check: the three modules' docstrings state the purity rule;
`wheresat_records` imports nothing from `git_donkey`; no dependency added.
Recovery: the modules are unreferenced; revert.
Remaining gaps: no Git, no GitHub, no command.

**EP-M4 — the two Git ports.**
Outcome: `git_donkey/wheresat_graph.py` defines the read-only
`WheresatGraph` protocol and its GitPython implementation;
`git_donkey/wheresat_refs.py` defines `WheresatRefWriter`, the only object in
the command that can write. `tests/integration/test_wheresat_read_only.py`,
`test_wheresat_ranges.py`, and `test_wheresat_durability.py` pass,
discharging INV-1, INV-6's real half, and INV-8.
Requirements: REQ-read-only, REQ-fork-point (local half).
Acceptance evidence: the read-only matrix passes and its companion negative
control — the deliberately mutating writer — fails as intended.
Conformance check: no module other than `wheresat_refs` can write; the
evidence namespace is the only ref prefix written; `--op-id` values are
validated before reaching either port.
Recovery: revert; nothing references either module yet.
Remaining gaps: no GitHub, no command.

**EP-M5 — collection, rendering, and the command line, local evidence only.
Shippable plateau.**
Outcome: `git_donkey/wheresat_collect.py` owns the ordered evidence
pipeline; `git_donkey/wheresat_report.py` renders the text report;
`git_donkey/wheresat.py` exposes `run_git_wheresat(...) -> int` and records
observations; `git_donkey/cli.py` gains `_wheresat_app` and `git_wheresat()`;
`pyproject.toml` gains the console script, the `rst2man` line, and the
shared-data mapping; `docs/man/git-wheresat.rst` documents every Cyclopts
parameter; `docs/users-guide.md`, `README.md`, and `docs/contents.md` gain
their entries. Gates needing a parent pull request report `INDETERMINATE`,
so the command answers from a receipt and local refs and otherwise exits `3`
saying what it could not determine.
Requirements: REQ-receipt-read, REQ-refusal, REQ-fork-point.
Acceptance evidence: `git wheresat --help` prints the synopsis;
`tests/unit/test_manpage_sources.py` passes, proving every parameter is
documented; snapshot tests cover the established, unresolved, and
indeterminate text reports; running the command in the
`squash_merged_stack()` fixture with a receipt present exits `0` and prints
the boundary.
Conformance check: a new console script is introduced, which is intended;
the observability vocabulary additions match those listed in
`Interfaces and dependencies` exactly; no existing signature changed.
Recovery: revert; `uv sync` clears an installed `git-wheresat` shim.
Remaining gaps: receipts cannot be written; no GitHub evidence; no `--json`;
no behavioural suite.
Note: this is a deliberate release boundary. It may be merged as its own
pull request so that the local-evidence command and its conventions are
reviewed separately from the GitHub surface. Doing so is recommended.

**EP-M6 — receipt recording.**
Outcome: `--record` and `--expected-old` write `refs/stack-bases/<branch>`
and the `branch.<branch>.stackParent`, `.stackBaseRecordedFrom`, and
`.stackBaseEvidence` config keys, with the create-only and expected-old
semantics of INV-7, and nothing else. The users' guide explains when to
record a receipt, why a birth-time marker goes stale after a later parent
integration, and that `git plonk --hard` on a parent worktree forecloses
fork-point recovery for its children.
Requirements: REQ-receipt-write.
Acceptance evidence: `tests/integration/test_wheresat_receipt_write.py`
passes all five cases of INV-7.
Conformance check: INV-1's matrix still passes unchanged, and a separate
assertion proves `--record` is the only path that constructs the writer for
anything other than a fetch.
Recovery: revert; a written receipt is removed with `git update-ref -d` and
`git config --local --unset`.
Remaining gaps: no GitHub evidence; no `--json`; no behavioural suite.

**EP-M7 — GitHub evidence, machine-readable output, and the behavioural
suite.**
Outcome: `git_donkey/wheresat_github.py` defines `WheresatGitHub` and its
`github3.py` implementation, with its own token resolution that never
prompts. `--json` emits the versioned envelope on every exit code. Cassettes
recorded against real GitHub traffic cover a merged squash pull request, an
open pull request, a rate-limited response, and one commit-to-pull-request
association page. `tests/integration/features/git_wheresat.feature` and its
binder module pass. `docs/developers-guide.md` gains the module-boundaries
section, the `--json` convention, the cassette-recording procedure, and the
`stack`-field note; `docs/v0-2-0-migration-guide.md` gains a new-commands
entry.
Requirements: REQ-parent-pr, REQ-pr-head.
Acceptance evidence: `make test` passes with the cassettes replayed in
`none` record mode, so any unrecorded request fails; `uv run pytest
tests/unit/test_wheresat_github_faults.py -q` covers all seven error classes.
Conformance check: no live network access in the suite; the `Authorization`
header is filtered from every cassette; the association search is bounded and
reports truncation; no cassette was hand-edited.
Recovery: revert; cassettes are additive files.
Remaining gaps: none planned.

**EP-M8 — `git plonk` branch tombstones (separable).**
Outcome: before deleting a completed local branch,
`git_donkey/plonk.py::_GitWorktreeAdapter.delete_branch` writes
`refs/plonk-tombstones/<branch>` naming the deleted tip. `git wheresat`
consults tombstones as a `DERIVED` source. The users' guide and
`docs/plonk-cleanup-policy.md` record the behaviour.
Requirements: mitigates the `git plonk` risk.
Acceptance evidence: a behavioural scenario in which `git plonk --hard`
removes a parent worktree and deletes its branch, and `git wheresat` still
recovers the boundary from the tombstone.
Conformance check: this changes an existing command's observable behaviour,
so it needs its own users' guide and design-document updates; if the scope
tolerance is near, split it into its own pull request rather than dropping
it.
Recovery: revert; tombstone refs are inert.
Remaining gaps: none.

## Concrete steps

Run everything from the repository root:

```shell
cd "$(git rev-parse --show-toplevel)"
```

Set the log prefix once per shell so gate output is captured per branch.
Replace any `/` in the branch name, or `tee` will try to write into a
directory that does not exist:

```shell
BRANCH="$(git branch --show-current | tr '/' '-')"
LOG() { printf '/tmp/%s-git-donkey-%s.out' "$1" "$BRANCH"; }
```

Install and refresh the environment:

```shell
make build 2>&1 | tee "$(LOG build)"
```

Run one focused test while working red to green:

```shell
uv run pytest tests/unit/test_wheresat_policy.py -q 2>&1 | tee "$(LOG focus)"
```

Expected before implementation:

```plaintext
ImportError: cannot import name 'wheresat_policy' from 'git_donkey'
```

Run the full gate set before each commit, sequentially and never in
parallel, so the build cache is used:

```shell
make check-fmt 2>&1 | tee "$(LOG check-fmt)"
make lint      2>&1 | tee "$(LOG lint)"
make typecheck 2>&1 | tee "$(LOG typecheck)"
make test      2>&1 | tee "$(LOG test)"
```

For any commit that touches Markdown:

```shell
make markdownlint 2>&1 | tee "$(LOG markdownlint)"
make nixie        2>&1 | tee "$(LOG nixie)"
```

Check code health on the new files before committing, because the pull
request gate applies the same rules:

```shell
cs check git_donkey/wheresat_policy.py
cs delta origin/main
```

Record a cassette in EP-M7. Do this once, against real traffic, and never
edit the result by hand:

```shell
GITHUB_TOKEN="$(env -u GH_TOKEN gh auth token)" \
  uv run pytest tests/integration/test_wheresat_github.py \
  --record-mode=once -q 2>&1 | tee "$(LOG cassette)"
grep -c -i '^ *authorization:' tests/integration/cassettes/wheresat_*.yaml
```

The `grep` must print `0` for every file. Note that `GH_TOKEN` is unset
deliberately: an injected `GH_TOKEN` in this environment shadows the stored
`gh` session and returns HTTP 401.

Update a syrupy snapshot deliberately, never as a reflex:

```shell
uv run pytest tests/unit/test_wheresat_report.py --snapshot-update -q
```

Exercise the finished command against a scenario repository:

```shell
git wheresat --onto origin/main
```

Expected output for the established case:

```plaintext
🫏 git-wheresat: replay boundary established
  child            feature/child
                   tip 9f2c1ab4e5d6c7b8a9f0e1d2c3b4a5f6e7d8c9b0
  parent PR        leynos/git-donkey#123
                   squash-merged 2026-09-01T09:14:00Z
  integration      4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e
                   reachable from origin/main
  replay boundary  1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b  (exclusive)
  evidence         receipt (attested), corroborated by pull request head
  gates            8 applicable, 8 passed
  replaying 2 commits, excluding 2
  computed against target 7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d

  # back up the child tip first, then replay
  git update-ref refs/wheresat-backup/feature/child \
    9f2c1ab4e5d6c7b8a9f0e1d2c3b4a5f6e7d8c9b0
  git rebase --onto 7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d \
    1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b feature/child
  # undo: git reset --hard refs/wheresat-backup/feature/child

  Verify before running: the child tip must still be 9f2c1ab.
```

Expected output when the boundary cannot be established:

```plaintext
🫏 git-wheresat: replay boundary NOT established
  child            feature/child
                   tip 9f2c1ab4e5d6c7b8a9f0e1d2c3b4a5f6e7d8c9b0
  parent PR        leynos/git-donkey#123
                   squash-merged 2026-09-01T09:14:00Z
  integration      4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e
                   reachable from origin/main
  candidates
    1a2b3c4d…  merge-base      derived; no corroborating candidate
    7e8f9a0b…  patch-identity  inferred; cannot establish a boundary
  gates
    parent-history-intact          FAILED
      1a2b3c4d is not an ancestor of the parent head 5f6e7d8c; the parent
      was rewritten before it was merged, so this merge base is a trunk
      commit rather than the inherited boundary.
    replay-range-excludes-landed-work  INDETERMINATE
      the historical parent head could not be recovered
  unresolved
    No attested evidence survives, and the single derived candidate is
    contradicted by the parent-history-intact gate.
  no rebase command proposed. Next steps: supply --parent, restore the
  parent ref from a colleague's clone or a backup, or ask a maintainer to
  confirm the boundary.
```

## Validation and acceptance

Acceptance is behavioural. A reader should be able to run these and compare.

### Red-Green-Refactor evidence

- Red: `uv run pytest tests/unit/test_wheresat_properties.py -q` fails with
  `ImportError` for `git_donkey.wheresat_policy` before EP-M3, and each
  milestone's tests fail with a missing-module or missing-step error before
  that milestone's implementation.
- Green: the same command passes after the minimal implementation.
- Refactor: `make check-fmt`, `make lint`, `make typecheck`, and `make test`
  all pass after cleanup, in that order, each captured with `tee`.

### Behavioural specification

`tests/integration/features/git_wheresat.feature`, cut deliberately to seven
scenarios. Single-gate assertions such as "the pull request is still open"
and "the integration commit is absent from the target" belong in
parameterized policy tests against faulting stubs, not in user journeys; they
are covered by INV-4's per-gate obligation instead.

```gherkin
Feature: Locate the replay boundary for a squash-merged parent

  Background:
    Given a child branch stacked on a parent branch
    And the parent pull request was squash-merged into the trunk

  Scenario: Established by a recorded receipt
    Given a stack-base receipt recording the inherited boundary
    When I run git wheresat
    Then the report names the recorded commit as the exclusive replay boundary
    And the report proposes a backup ref and a rebase command with full object IDs
    And the command exits with status 0

  Scenario: Established by pull request head
    Given no stack-base receipt
    And the parent pull request head is an ancestor of the child branch
    When I run git wheresat
    Then the report names the pull request head as the exclusive replay boundary
    And the report cites pull request head ancestry as the establishing evidence
    And the command exits with status 0

  Scenario: Refusal after a rewritten parent
    Given no stack-base receipt
    And the parent branch was rebased before it was merged
    And no surviving ref or reflog records the historical parent tip
    When I run git wheresat
    Then the report names the parent-history-intact gate as the reason
    And the report proposes no rebase command
    And the command exits with status 1

  Scenario: Two inferred candidates remain unresolved
    Given only content-comparison evidence remains
    And two distinct commits match the squashed parent change
    When I run git wheresat with deep scanning enabled
    Then the report lists both candidates with their evidence tier
    And the report states the unresolved distinction between them
    And the report proposes no rebase command
    And the command exits with status 1

  Scenario: The parent pull request was opened from a fork
    Given the parent pull request was opened from a fork of the child repository
    When I run git wheresat
    Then the pull request head is fetched from the fork rather than from origin
    And the report names the pull request head as the exclusive replay boundary
    And the command exits with status 0

  Scenario: Shallow history cannot answer the ancestry question
    Given the repository history is shallow
    When I run git wheresat
    Then the report states that the ancestry check was indeterminate
    And the report does not state that the boundary is not an ancestor
    And the command exits with status 3

  Scenario: The run leaves the repository unchanged
    Given a stack-base receipt recording the inherited boundary
    When I run git wheresat
    Then no branch, tag, remote-tracking ref, index entry, or tracked file changes
    And the only new refs are under the evidence namespace
```

`tests/integration/features/git_wheresat_receipt.feature` (EP-M6):

```gherkin
Feature: Record a stack-base receipt

  Scenario: Recording a receipt for the first time
    Given a child branch with an established replay boundary and no receipt
    When I run git wheresat with recording enabled
    Then the stack-base ref names the established boundary
    And the branch configuration records the parent identity and the child tip

  Scenario: Refusing to overwrite an existing receipt
    Given a child branch with an existing stack-base receipt
    When I run git wheresat with recording enabled and no expected old value
    Then the existing receipt is unchanged
    And the command reports that an expected old object ID is required
    And the command exits with status 2

  Scenario: Updating a receipt with the correct expected old value
    Given a child branch with an existing stack-base receipt
    When I run git wheresat with recording enabled and the correct expected old value
    Then the stack-base ref names the new boundary

  Scenario: Refusing to record an unresolved result
    Given a child branch whose replay boundary cannot be established
    When I run git wheresat with recording enabled
    Then no stack-base ref is created
    And the command exits with status 1
```

### Quality criteria

- Tests: `make test` passes with no new failures, skips, or expected
  failures. Every scenario above is bound and runs.
- Verification: INV-1 through INV-8 are discharged by the artefacts named in
  `Verification plan`, each having failed first, and each negative control
  rejected for the intended reason. Every gate has a test in which that gate
  alone fails against a real repository or a recorded cassette.
- Lint and typecheck: `make check-fmt`, `make lint`, and `make typecheck`
  pass. `interrogate --fail-under 100` means every new public symbol carries
  a docstring.
- Documentation: `make markdownlint` and `make nixie` pass; the users' guide
  section, developers' guide section, README bullet, migration-guide entry,
  design document, ADR, and manual page are present and cross-linked from
  `docs/contents.md`.
- Code health: `cs check` reports 10.00 for each new module, and
  `cs delta origin/main` reports no decline.

### Quality method

Run the four code gates sequentially before every code commit and the two
Markdown gates before every documentation commit, capturing each with `tee`
to `/tmp`. Delegate full gate runs to the `scrutineer` sub-agent rather than
running them inline, and read the cited log on failure instead of re-running
the gate.

## Idempotence and recovery

Every step is safely repeatable. `make build` is idempotent. Test runs create
only temporary repositories under pytest's `tmp_path`.

The command writes only refs under `refs/wheresat/`. Two of those are
deliberate and durable: `refs/wheresat/parent-head/<owner>/<repo>/<number>`
caches an immutable fetched pull request head, so a second run on the same
pull request performs no fetch at all; and `refs/wheresat/boundary/<branch>`
is retained only when INV-8 finds the established boundary is otherwise
unreachable. Per-run namespaces under `refs/wheresat/op/<op-id>/` are deleted
in a `finally` block.

Do **not** sweep the whole namespace. Refs live in the common ref store, so
every worktree of a checkout shares `refs/wheresat/`, and a global delete
will destroy a sibling worktree's in-flight evidence mid-fetch, or the
durable boundary ref another run is relying on. To clear stale per-run
namespaces only:

```shell
git for-each-ref --format='%(refname) %(committerdate:unix)' \
  'refs/wheresat/op/**' \
  | awk -v cutoff="$(( $(date +%s) - 3600 ))" '$2 < cutoff {print $1}' \
  | xargs -r -n1 git update-ref -d
```

A receipt written by EP-M6 is removed with:

```shell
git update-ref -d "refs/stack-bases/$BRANCH"
git config --local --remove-section "branch.$BRANCH" 2>/dev/null || true
```

Prefer unsetting the three individual keys if the branch section holds other
settings.

Nothing in this plan rewrites history, force-pushes, or deletes a branch, so
there is no destructive step requiring a backup. If a milestone must be
abandoned, reverting its commits restores the previous plateau, because no
later milestone depends on a partially applied earlier one.

Note for anyone working in a `git-donkey` worktree: the shared stash stack
means bare `git stash` and `git stash pop` are unsafe here. Set work aside
with a temporary commit instead.

## Artefacts and notes

The evidence fetch, confined to the cache namespace:

```shell
DEST="refs/wheresat/parent-head/$PARENT_OWNER/$PARENT_REPO/$PARENT_PR"
git rev-parse --verify --quiet "$DEST^{commit}" >/dev/null || \
  git fetch --no-prune --no-tags --no-write-fetch-head "$PR_REMOTE" \
    "refs/pull/$PARENT_PR/head:$DEST"
PARENT_HEAD="$(git rev-parse --verify "$DEST^{commit}")"
```

The ancestry questions the gates ask, in plumbing form. Measured on Git
2.52.0 in this worktree, `--is-ancestor` returns `0`, `1`, or `128`:

```shell
git merge-base --is-ancestor "$PARENT_HEAD" "$CHILD_TIP"   # gate 4 source
git merge-base --is-ancestor "$LANDED"      "$TARGET"      # gate 3
git merge-base --is-ancestor "$CANDIDATE"   "$PARENT_HEAD" # gate 6
git merge-base --all         "$PARENT_HEAD" "$CHILD_TIP"   # advanced parent
```

The cumulative comparison, which is the correct shape for a squash. A squash
commit's diff equals the combined diff of the parent range, so the decisive
test is one comparison per candidate, not one per commit:

```shell
# cheap first pass: does the candidate's tree match the squash commit's tree?
git rev-parse "$CANDIDATE^{tree}" "$LANDED^{tree}"

# only for survivors: does the cumulative patch match?
git diff "$(git merge-base "$CANDIDATE" "$TRUNK_BEFORE")" "$CANDIDATE" \
  | git patch-id --stable
git show "$LANDED" | git patch-id --stable
```

The receipt, written only under `--record`:

```shell
git config --local "branch.$BRANCH.stackParent" "v1:$PARENT_REPOSITORY#$PARENT_PR"
git config --local "branch.$BRANCH.stackBaseRecordedFrom" "$CHILD_TIP"
git config --local "branch.$BRANCH.stackBaseEvidence" "pull-request-head"
git update-ref --create-reflog "refs/stack-bases/$BRANCH" "$OLD_BASE" ""
```

The trailing empty string is the expected-old value, which makes that form
create-only. An update supplies the current object ID instead.

The shared record, for cross-clone recovery, written by a human into the
child pull request body:

```plaintext
Stack parent: owner/repository#123
Replay boundary (exclusive): <full commit object ID>
```

## Interfaces and dependencies

No new package dependency. Everything needed is already declared: `cyclopts`
for the command line, `GitPython` for repository access, `github3.py` and
`loctocat` for GitHub, and `pytest`, `pytest-bdd`, `hypothesis`, `syrupy`,
and `vcrpy` for tests.

House conventions that constrain the code, from `pyproject.toml` and
`.pylintrc-df12.toml`: Ruff line length 88, McCabe complexity at most 8, at
most 4 arguments, at most 10 locals, at most 2 boolean operators in an
expression; Pylint module length at most 800 lines and at most 70 statements
per function. Bare `from dataclasses import ...`, `from typing import ...`,
and `from enum import ...` are banned — import the module and qualify. Use
frozen, slotted dataclasses for value types, `enum.StrEnum` for closed
vocabularies, `typing.Protocol` for adapter interfaces, and PEP 695 syntax
for generics and type aliases.

### `git_donkey/wheresat_records.py`

Shared value types. No Git, filesystem, network, or process access, and no
imports from anywhere else in `git_donkey`. Every type any other wheresat
module exchanges is defined here, so no adapter's types leak into the policy.

```python
@dataclasses.dataclass(frozen=True, slots=True)
class PullRequestIdentity:
    """A pull request named by repository and number."""

    repository: str
    number: int


class Ancestry(enum.StrEnum):
    """Answer to one ancestry question, including "could not tell"."""

    ANCESTOR = "ancestor"
    NOT_ANCESTOR = "not-ancestor"
    UNKNOWN = "unknown"


class EvidenceTier(enum.StrEnum):
    """How much weight a boundary candidate may carry."""

    ATTESTED = "attested"
    DERIVED = "derived"
    INFERRED = "inferred"


class EvidenceKind(enum.StrEnum):
    """Where a boundary candidate came from."""

    RECEIPT = "receipt"
    SHARED_RECORD = "shared-record"
    PULL_REQUEST_HEAD = "pull-request-head"
    MERGE_BASE = "merge-base"
    FORK_POINT = "fork-point"
    TREE_IDENTITY = "tree-identity"
    PATCH_IDENTITY = "patch-identity"


TIERS: typ.Final[typ.Mapping[EvidenceKind, EvidenceTier]]
"""The single place the tier of each evidence kind is decided."""


class ParentSource(enum.StrEnum):
    """Where the parent pull request identity came from."""

    EXPLICIT = "explicit"
    RECEIPT_CONFIG = "receipt-config"
    FORGE_STACK = "forge-stack"
    SHARED_RECORD = "shared-record"
    ASSOCIATION_SEARCH = "association-search"


@dataclasses.dataclass(frozen=True, slots=True)
class AttestedCandidate:
    """A boundary recorded by a deliberate act naming the exact commit."""

    commit: str
    kind: EvidenceKind
    source: str
    supporting: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class DerivedCandidate:
    """A boundary computed from surviving history; needs corroboration."""

    commit: str
    kind: EvidenceKind
    source: str
    supporting: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class InferredCandidate:
    """A boundary suggested by content comparison; never establishing."""

    commit: str
    kind: EvidenceKind
    source: str
    supporting: tuple[str, ...] = ()


type Candidate = AttestedCandidate | DerivedCandidate | InferredCandidate
type Establishing = AttestedCandidate | DerivedCandidate


class GateOutcome(enum.StrEnum):
    """Result of one validation gate."""

    PASSED = "passed"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


@dataclasses.dataclass(frozen=True, slots=True)
class GateResult:
    """One named gate, its outcome, and why."""

    name: str
    outcome: GateOutcome
    detail: str


@dataclasses.dataclass(frozen=True, slots=True)
class ParentPullRequest:
    """The parent pull request's merge state and refs."""

    identity: PullRequestIdentity
    merged: bool
    merged_at: str | None
    head_sha: str
    head_ref: str
    head_repository: str
    base_ref: str
    base_repository: str
    landed: str | None
    stacked: bool


@dataclasses.dataclass(frozen=True, slots=True)
class Receipt:
    """A parsed local receipt, or the reason it could not be used."""

    boundary: str
    parent: PullRequestIdentity | None
    recorded_from: str | None
    recorded_evidence: EvidenceKind | None


@dataclasses.dataclass(frozen=True, slots=True)
class SharedRecord:
    """A stack parent and replay boundary parsed from a pull request body."""

    parent: PullRequestIdentity
    boundary: str


@dataclasses.dataclass(frozen=True, slots=True)
class BoundaryRequest:
    """Everything the user asked for, resolved to immutable object IDs."""

    branch: str
    child_tip: str
    target: str
    parent: PullRequestIdentity | None
    deep: bool
    offline: bool


@dataclasses.dataclass(frozen=True, slots=True)
class GraphFacts:
    """Every graph answer the assessment needs, collected eagerly.

    This record holds data, never callables and never an adapter handle, so
    the assessment cannot reach the repository and a property test can build
    an arbitrary graph without one.
    """

    parent_head: str | None
    landed: str | None
    ancestry: typ.Mapping[tuple[str, str], Ancestry]
    range_contents: typ.Mapping[str, tuple[str, ...]]
    range_minus_parent: typ.Mapping[str, tuple[str, ...]]
    cumulative_patch: typ.Mapping[str, str | None]
    landed_patch: str | None
    receipt_recorded_from: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class Established:
    """A boundary that cleared every applicable gate."""

    old_base: str
    support: tuple[Establishing, ...]
    included: tuple[str, ...]
    excluded: tuple[str, ...]
    included_truncated: bool
    excluded_truncated: bool
    gates: tuple[GateResult, ...]
    durable_ref: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class Unresolved:
    """No candidate could establish a boundary, and the evidence is complete."""

    candidates: tuple[Candidate, ...]
    gates: tuple[GateResult, ...]
    reasons: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class Indeterminate:
    """The repository or the forge could not answer."""

    candidates: tuple[Candidate, ...]
    gates: tuple[GateResult, ...]
    reasons: tuple[str, ...]


type Assessment = Established | Unresolved | Indeterminate

EXIT_CODES: typ.Final[typ.Mapping[type, int]]
"""0 established, 1 unresolved, 2 usage or environment, 3 indeterminate."""
```

`Established.support` is typed `tuple[Establishing, ...]`, so constructing an
`Established` from an `InferredCandidate` is a type error rather than a test
failure. That is the type-level half of INV-2.

### `git_donkey/wheresat_receipts.py`

Parsing and rendering of the receipt ref, the `branch.<name>.*` keys, and the
shared-record block. Pure. Named for the formats it owns, not for the
evidence model, which lives in `wheresat_records`.

```python
@dataclasses.dataclass(frozen=True, slots=True)
class ReceiptAbsent:
    """No stack-base ref and no branch configuration."""


@dataclasses.dataclass(frozen=True, slots=True)
class ReceiptMalformed:
    """A half-receipt, an unknown version, or an unparsable parent."""

    reason: str


type ReceiptResult = Receipt | ReceiptAbsent | ReceiptMalformed


def parse_receipt(ref_value: str | None, config: typ.Mapping[str, str]) -> ReceiptResult:
    """Reconcile the stack-base ref and branch config into one receipt.

    A ref with no config, config with no ref, an unrecognized version
    prefix, or a `stackParent` value that is not `v1:owner/repo#123` all
    yield `ReceiptMalformed` with a reason. A half-receipt is never a
    boundary.
    """


def parse_pull_request_identity(text: str) -> PullRequestIdentity | None:
    """Parse `owner/repository#123`, returning None when unrecognized."""


@dataclasses.dataclass(frozen=True, slots=True)
class SharedRecordAbsent:
    """The body carries no stack-parent or replay-boundary line."""


@dataclasses.dataclass(frozen=True, slots=True)
class SharedRecordMalformed:
    """A line matched but its value did not, with the reason why."""

    reason: str


@dataclasses.dataclass(frozen=True, slots=True)
class SharedRecordAmbiguous:
    """Two or more disagreeing occurrences; never silently resolved."""

    records: tuple[SharedRecord, ...]


type SharedRecordResult = (
    SharedRecord | SharedRecordAbsent | SharedRecordMalformed | SharedRecordAmbiguous
)


def parse_shared_record(body: str) -> SharedRecordResult:
    """Extract a stack parent and replay boundary from a pull request body.

    The grammar is anchored per line and tolerates leading `-`, `*`, `>`,
    and `**` decoration. Object IDs must be full 40 or 64 hexadecimal
    characters; abbreviations are rejected rather than resolved. Lines
    inside fenced code blocks and block quotes are skipped. Exactly one
    occurrence of each field is required; two disagreeing occurrences yield
    `SharedRecordAmbiguous` rather than a silent choice.
    """


def render_shared_record(record: SharedRecord) -> str:
    """Render a shared-record block for pasting into a pull request body.

    `parse_shared_record(render_shared_record(r))` must return `r`; this
    round-trip is a property test, and it is why the renderer exists even
    though no command emits it. The users' guide presents its output as
    copy-paste text.
    """


def validate_op_id(value: str) -> str:
    """Return `value` when it is a safe ref path component, else raise.

    Must match `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`. This is validated in a
    pure module, before any adapter sees it, because the value is
    interpolated into a fetch refspec destination where `:` is the refspec
    separator and a leading `-` is argument injection.
    """
```

### `git_donkey/wheresat_policy.py`

The gates and the assessment. This module contains no GitPython, filesystem,
network, or process access.

```python
def gate_results(
    request: BoundaryRequest,
    candidate: Candidate,
    facts: GraphFacts,
    parent: ParentPullRequest | None,
) -> tuple[GateResult, ...]:
    """Evaluate every applicable gate against one candidate boundary."""


def ancestry_outcome(observed: Ancestry, *, expect: Ancestry) -> GateOutcome:
    """Map an ancestry answer to a gate outcome for the expected polarity.

    `Ancestry.UNKNOWN` always yields `INDETERMINATE`. This is the single
    place where "an error is not a negative answer" is decided, and it lives
    in the pure module so a truth table can reach it.
    """


def assess(
    request: BoundaryRequest,
    candidates: tuple[Candidate, ...],
    facts: GraphFacts,
    parent: ParentPullRequest | None,
) -> Assessment:
    """Return the established boundary, or why one could not be chosen."""


def may_establish(support: typ.Sequence[Candidate]) -> bool:
    """Return whether this support set is permitted to establish a boundary.

    True for one attested candidate, or two candidates from independent
    sources naming the same commit. Always false when every candidate is
    inferred.
    """
```

### `git_donkey/wheresat_collect.py`

The ordered evidence pipeline. Precedence is the order of a module-level
tuple, so a unit test can assert it against the ADR rather than against the
reading order of a long function.

```python
@dataclasses.dataclass(frozen=True, slots=True)
class CollectionContext:
    """Read-only inputs every evidence source shares."""

    request: BoundaryRequest
    parent: ParentPullRequest | None
    graph: WheresatGraph
    github: WheresatGitHub | None


@dataclasses.dataclass(frozen=True, slots=True)
class CollectionResult:
    """What one source produced, or why it produced nothing."""

    candidates: tuple[Candidate, ...]
    indeterminate_reasons: tuple[str, ...] = ()
    truncated: bool = False


class EvidenceSource(typ.Protocol):
    """One rung of the evidence ladder."""

    kind: EvidenceKind

    def collect(self, context: CollectionContext) -> CollectionResult:
        """Return candidates, or the reason none could be obtained."""


SOURCES: typ.Final[tuple[EvidenceSource, ...]]
"""Evidence sources in precedence order; asserted against ADR-004."""


MAX_CANDIDATES: typ.Final = 32
"""Cap on the candidate set. Exceeding it records a truncation reason."""
```

### `git_donkey/wheresat_graph.py`

The read-only Git port and its GitPython implementation. Nothing in this
module can write.

```python
class WheresatGraph(typ.Protocol):
    """Read-only Git surface required to collect boundary evidence."""

    def resolve(self, rev: str) -> str:
        """Return the full commit object ID for a revision."""

    def is_ancestor(self, ancestor: str, descendant: str) -> Ancestry:
        """Answer one ancestry question, or report that Git could not."""

    def merge_bases(self, left: str, right: str) -> tuple[str, ...]:
        """Return every best common ancestor of two commits."""

    def fork_point(self, upstream_ref: str, head: str) -> str | None:
        """Return the reflog-derived fork point, or None when unavailable."""

    def reflog(self, ref: str, *, limit: int = 100) -> tuple[str, ...]:
        """Return up to `limit` reflog entries for a ref, newest first."""

    def commits_in_range(
        self, exclude: str, include: str, *, not_reachable_from: str | None = None
    ) -> tuple[str, ...]:
        """Return commits reachable from include and not from exclude."""

    def tree_of(self, rev: str) -> str:
        """Return the tree object ID of a commit."""

    def cumulative_patch_identifier(self, base: str, tip: str) -> str | None:
        """Return one stable patch identifier for the whole `base..tip` diff.

        One `git diff | git patch-id --stable` pipeline per call. A squash is
        an N-to-1 relationship, so the comparison that matters is cumulative;
        never compute one identifier per commit. Returns None when the range
        has no diff.
        """

    def is_reachable_from_durable_ref(self, commit: str) -> bool:
        """Return whether any ref outside the evidence namespace reaches it."""
```

### `git_donkey/wheresat_refs.py`

The only writing capability in the command. `run_git_wheresat` constructs it
when a fetch or a receipt write is required, and not otherwise, so the
default path has no object that can write.

```python
class WheresatRefWriter(typ.Protocol):
    """The only Git surface in this command that mutates anything."""

    def fetch_evidence(self, remote: str, source_ref: str, destination: EvidenceRef) -> None:
        """Fetch one ref into the evidence namespace and nowhere else."""

    def retain_boundary(self, branch: str, commit: str) -> str:
        """Keep a durable ref for an otherwise unreachable boundary (INV-8)."""

    def release(self, op_id: str) -> None:
        """Delete this run's per-run namespace. Called from a finally block."""

    def write_receipt(self, branch: str, receipt: Receipt, expected_old: str | None) -> None:
        """Write the stack-base ref and branch config, honouring INV-7."""
```

`EvidenceRef` is a `typing.NewType` over `str` — a plain assignment, not a
`type` alias statement, because `NewType` must be assigned — constructed only
by the two factory functions below, so a raw string can never reach a refspec
destination:

```python
EvidenceRef = typ.NewType("EvidenceRef", str)


def per_run_ref(op_id: str, name: str) -> EvidenceRef:
    """Return `refs/wheresat/op/<validated op-id>/<name>`."""


def parent_head_ref(identity: PullRequestIdentity) -> EvidenceRef:
    """Return the durable cache ref for a pull request head."""
```

### `git_donkey/wheresat_github.py`

The GitHub port and its `github3.py` implementation, with its own token
resolution that reads `GITHUB_TOKEN`, `GH_TOKEN`, then the cached credentials
file, and then **gives up** with exit code `2` — it never calls
`loctocat` and never prompts.

```python
REQUEST_TIMEOUT_SECONDS: typ.Final = 10.0
NETWORK_BUDGET_SECONDS: typ.Final = 60.0
ASSOCIATION_SEARCH_LIMIT: typ.Final = 20


class WheresatGitHub(typ.Protocol):
    """GitHub surface required to identify and describe the parent."""

    def pull_request(self, identity: PullRequestIdentity) -> ParentPullRequest:
        """Return merge state, head, base, integration commit, and stack flag."""

    def pull_request_body(self, identity: PullRequestIdentity) -> str:
        """Return a pull request body, for shared-record extraction."""

    def stack_parent(self, identity: PullRequestIdentity) -> PullRequestIdentity | None:
        """Return the pull request below this one in a native GitHub stack."""

    def associated_pull_requests(
        self, repository: str, commits: typ.Sequence[str]
    ) -> AssociationPage:
        """Return pull requests associated with up to `ASSOCIATION_SEARCH_LIMIT` commits."""
```

```python
@dataclasses.dataclass(frozen=True, slots=True)
class AssociationPage:
    """Pull requests associated with a bounded set of commits."""

    associations: typ.Mapping[str, tuple[PullRequestIdentity, ...]]
    commits_examined: int
    truncated: bool
```

`AssociationPage` carries `truncated: bool` and `commits_examined: int`, so a
bounded search reports truncation rather than silently narrowing. Exceeding
the limit is a hard stop advising `--parent`, not a quiet partial answer.

`stack_parent` reads `pull_request.as_dict().get("stack")`, because
`github3.py` 4.0.1 does not model the field, and falls back to
`GET /repos/{owner}/{repo}/stacks` through the library's `requests.Session`
so vcrpy still intercepts it.

### `git_donkey/wheresat_report.py`

Pure rendering.

```python
RENDER_COMMIT_LIMIT: typ.Final = 20
"""Commits listed per range before the report prints a truncation tail."""

JSON_SCHEMA: typ.Final = "git-wheresat/1"


def render_text(assessment: Assessment, request: BoundaryRequest) -> str:
    """Render the human-readable report, including the gate table."""


def render_json(assessment: Assessment, request: BoundaryRequest) -> str:
    """Render the versioned machine-readable envelope."""


def render_error_json(code: int, message: str) -> str:
    """Render the envelope for a usage or environment failure."""
```

The JSON envelope is an explicit projection, never `dataclasses.asdict`, so
renaming a private field cannot change the wire format:

```json
{
  "schema": "git-wheresat/1",
  "verdict": "established",
  "exitCode": 0,
  "error": null,
  "child": {"branch": "feature/child", "tip": "9f2c1ab…"},
  "target": "7c8d9e0f…",
  "parent": {"repository": "leynos/git-donkey", "number": 123},
  "parentHead": "5f6e7d8c…",
  "landed": "4d5e6f7a…",
  "oldBase": "1a2b3c4d…",
  "durableRef": null,
  "included": {"commits": ["…"], "count": 2, "truncated": false},
  "excluded": {"commits": ["…"], "count": 2, "truncated": false},
  "support": [{"commit": "1a2b3c4d…", "kind": "receipt", "tier": "attested"}],
  "candidates": [],
  "gates": [{"name": "parent-merged", "outcome": "passed", "detail": "…"}],
  "reasons": [],
  "rebaseCommand": "git rebase --onto 7c8d9e0f… 1a2b3c4d… feature/child",
  "backupRef": "refs/wheresat-backup/feature/child"
}
```

Contract rules, to be written into `docs/developers-guide.md`: a key may be
added in a later minor revision, but never removed or retyped without
incrementing the schema string; `verdict` is one of `established`,
`unresolved`, `indeterminate`, or `error`; and an object is emitted on
**every** exit code, including `2`, so a consumer never receives unparsable
output. When `--json` is set, the command must not route failures through
`helpers._die`, which prints prose.

### `git_donkey/wheresat.py`

Orchestration, observability, and the exit-code contract.

```python
def run_git_wheresat(
    options: WheresatOptions,
    graph: WheresatGraph | None = None,
    github: WheresatGitHub | None = None,
) -> int:
    """Locate the replay boundary and report it.

    Returns 0 when established, 1 when unresolved, 2 for a usage or
    environment error, and 3 when the repository or the forge could not
    answer.
    """
```

```python
@dataclasses.dataclass(frozen=True, slots=True)
class WheresatOptions:
    """Every command-line input, before resolution to object IDs."""

    branch: str | None = None
    onto: str | None = None
    parent: str | None = None
    remote: str | None = None
    limit: int = 20
    heuristic_window: int = 200
    no_fetch: bool = False
    offline: bool = False
    deep: bool = False
    explain: bool = False
    json: bool = False
    op_id: str | None = None
    record: bool = False
    expected_old: str | None = None
```

`WheresatOptions` is a frozen dataclass flattened onto the command line with
`typ.Annotated[WheresatOptions, Parameter(name="*")]`, following the
`_PullOptions` precedent at `git_donkey/cli.py:32-63`. Injecting the two
ports keeps the argument count within the Ruff limit of four and lets tests
substitute faulting or recording doubles.

The command warns, without failing, when a rebase or merge is already in
progress or the child worktree is dirty: the command is safe to run in that
state, but the command it prints is not safe to run.

### Command surface

```plaintext
git wheresat [--branch NAME] [--onto REV] [--parent OWNER/REPO#N]
             [--remote NAME] [--limit N] [--heuristic-window N]
             [--no-fetch] [--offline] [--deep] [--explain] [--json]
             [--op-id ID] [--record] [--expected-old OID]
```

- `--branch NAME` — the child branch. Defaults to the current branch.
- `--onto REV` — the replay target. Defaults to the principal remote's
  advertised default branch, via
  `git_donkey.remote_default.discover_default_branch`, captured immediately
  as an immutable object ID and echoed in the report.
- `--parent OWNER/REPO#N` — the parent pull request. Overrides discovery, and
  is required when the bounded association search would otherwise be
  exceeded.
- `--remote NAME` — the remote holding the child. Defaults to the principal
  remote. The parent's head is fetched from the pull request's own
  repository, not from this remote.
- `--limit N` — commits examined by the commit-to-pull-request association
  search. Default 20. Exceeding it is a hard stop advising `--parent`, never
  a silent truncation.
- `--heuristic-window N` — trunk commits scanned when `--deep` is set.
  Default 200, measured backwards from the target. The report states the
  window scanned so a partial scan never reads as a complete one.
- `--no-fetch` — perform no Git transport. GitHub queries still run.
- `--offline` — perform no network access of any kind. The receipt plus local
  ancestry must suffice; otherwise the command exits `3` naming the gates it
  could not evaluate. This is the fast path: it should complete in well under
  a second with no round trips.
- `--deep` — also derive tree-identity and cumulative-patch-identity
  candidates. This is a **cost control, not a semantics switch**: inferred
  evidence can never establish a boundary, so `--deep` can add candidates to
  the report but can never change the verdict. It is off by default because
  the scan is linear in `--heuristic-window`.
- `--explain` — render the full gate table even on the established path. The
  table is always rendered on the unresolved and indeterminate paths.
- `--json` — emit the versioned envelope on standard output for every exit
  code.
- `--op-id ID` — name the per-run evidence namespace. A test seam; the
  default is a `uuid4`. Validated against
  `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` before use.
- `--record` — write the receipt. The only write outside the evidence
  namespace. Refuses unless the result was established from attested
  evidence.
- `--expected-old OID` — required to update an existing receipt.

Exit codes: `0` established; `1` the boundary could not be established from
complete evidence, which is a legitimate result and not a malfunction; `2` a
usage, configuration, or credential error; `3` indeterminate — the
repository or the forge could not answer, and the environment needs repair.
The departure from the three-code convention used by `git incoming` and
`git outgoing` is deliberate and recorded in `Decision log`.

### Observability additions

Add to `git_donkey/observability.py`, and to no other `typing.Literal`:

- `Operation`: `parent_identification`, `evidence_collection`,
  `boundary_assessment`, `evidence_fetch`, `receipt_write`.
- `Outcome`: reuse `success`, `failure`, `started`, `selected`, `rejected`.
- New label type `EvidenceTierLabel = typ.Literal["attested", "derived",
  "inferred"]`.
- New label type `WheresatVerdictLabel = typ.Literal["established",
  "unresolved", "indeterminate"]`.
- Add to `ErrorKind`: `github_api_error`, `shallow_history`,
  `credential_unavailable`.

Budget roughly 150 production lines and 350 test lines for this, in EP-M5.
`tests/observability_helpers.py::declared_attribute_values()` derives the
bounded vocabulary from these type aliases, so the additions are pinned
automatically.

### Test-infrastructure changes

- `tests/conftest.py` gains a Hypothesis profile: `deadline=None`,
  `max_examples=50` by default and 500 under a `nightly` profile selected by
  `HYPOTHESIS_PROFILE`. Registered because the library default of
  `deadline=200ms` is incompatible with any property that touches a
  subprocess. Documented in `docs/developers-guide.md` under
  `## Test infrastructure`.
- Integration tests that build repositories carry
  `@pytest.mark.timeout(120)`, overriding the global `timeout = 30`.
- `tests/git_repo_helpers.py` gains `squash_merged_stack()`,
  `advanced_parent_stack()`, and `rewritten_parent_stack()` in EP-M2.
- New cassettes use `allow_playback_repeats=True` where one cassette serves
  several parameterized cases.
- New syrupy snapshots use a `syrupy.matchers.path_type` matcher redacting
  object IDs and absolute paths, so `ambrleaks` stays green and snapshots do
  not depend on a fixture's random commit hashes.

### Changes to existing files

- `git_donkey/cli.py`: add `_wheresat_app`, `_wheresat_cli`, and
  `git_wheresat()`, following the `_plonk_app` pattern.
- `git_donkey/observability.py`: the vocabulary additions above.
- `pyproject.toml`: `git-wheresat = "git_donkey.cli:git_wheresat"` in
  `[project.scripts]`, one `rst2man` entry in the `build-scripts.scripts`
  array, and one `docs/man/git-wheresat.1` shared-data mapping.
- `README.md`: a bullet in the command overview.
- `docs/users-guide.md`: a `## git wheresat` section and a row in
  `## Command overview`.
- `docs/developers-guide.md`: a `## git-wheresat module boundaries` section,
  the `--json` output convention, the Hypothesis-profile convention, the
  cassette-recording procedure, and the `github3.py` `as_dict()` note.
- `docs/v0-2-0-migration-guide.md`: a new-commands entry, following the
  existing `## New comparison commands` section.
- `docs/contents.md`: entries for the design document and the ADR.

### New documents

- `docs/man/git-wheresat.rst` — following `docs/man/git-plonk.rst`, with
  `SYNOPSIS`, `DESCRIPTION` including the four-value exit-status list,
  `OPTIONS`, `EXAMPLES`, and `SEE ALSO`. Every Cyclopts parameter must
  appear, because `tests/unit/test_manpage_sources.py` cross-checks them.
- `docs/squash-restack-boundary-recovery.md` and
  `docs/adr-004-squash-restack-evidence-precedence.md`, as described in
  `Conformance basis`.

## Signposts

Read these before starting.

Repository documentation:

- `AGENTS.md` — code style, test-first delivery, quality gates, commit
  discipline, and the Markdown rules.
- `docs/documentation-style-guide.md` — prose conventions, the ADR template
  at lines 267-316, Mermaid captioning, and the 80-column wrap.
- `docs/developers-guide.md` — module boundaries per command, observability
  at lines 343-372, test infrastructure and the cassette rule at lines
  526-610, and the manual-page contract.
- `docs/manpages-design.md` — how manual pages are built and installed.
- `docs/plonk-cleanup-policy.md` and `docs/default-base-and-pull-modes.md` —
  the two existing design documents, and the shape to copy: a captioned
  Mermaid figure, a `## Decision` section, topic sections, then a
  `## Verification contract`.
- `docs/users-guide.md` — the section shape for a command.
- `.rules/python-00.md`, `.rules/python-typing.md`,
  `.rules/python-return.md`, and
  `.rules/python-exception-design-raising-handling-and-logging.md` — the
  binding Python conventions.

External references:

- [git-rebase](https://git-scm.com/docs/git-rebase) — explicit range
  transplantation with `--onto`, and `--update-refs`.
- [git-merge-base](https://git-scm.com/docs/git-merge-base) —
  `--is-ancestor`, `--all`, and the fork-point discussion.
- [git-patch-id](https://git-scm.com/docs/git-patch-id) — stable identifiers
  and what they ignore.
- [git-cherry](https://git-scm.com/docs/git-cherry) — Git's own patch-id
  equivalence check against an upstream, and the closest thing in core Git to
  the inferred rung here.
- [git-update-ref](https://git-scm.com/docs/git-update-ref) — expected-old
  checks.
- [GitHub REST: pulls](https://docs.github.com/en/rest/pulls/pulls) — merge
  state and `merge_commit_sha` semantics.
- [GitHub REST: stacked pull
  requests](https://docs.github.com/en/rest/pulls/stacks) and [About stacked
  pull
  requests](https://docs.github.com/en/pull-requests/get-started/about-stacked-prs)
  — the native stack relationship and its same-repository limitation.
- [GitHub REST:
  commits](https://docs.github.com/en/rest/commits/commits#list-pull-requests-associated-with-a-commit)
  — commit-to-pull-request association.
- [Checking out pull requests
  locally](https://docs.github.com/en/pull-requests/how-tos/review-pull-requests/checking-out-pull-requests-locally)
  — recovering inactive pull request heads.

Prior art, all of which makes the same bet in different ways — record the
relationship rather than reconstruct it:

- [git-machete](https://git-machete.readthedocs.io/) — `machete.squashMergeDetection`
  offers `none`, `simple` (compare trees) and `exact` (compare patches), the
  same ladder this command exposes under `--deep`, and documents `exact` as
  having a significant performance impact on large repositories. Its
  `machete.overrideForkPoint.<branch>.to` key is a receipt by another name.
- [github/gh-stack](https://github.com/github/gh-stack) — GitHub's own
  extension stores stack metadata in `.git/gh-stack` and switches its rebase
  to `--onto` when a lower pull request merges. It performs no forensics
  because it never loses the parent.
- [Graphite command reference](https://graphite.com/docs/command-reference) —
  explicit parent metadata, with `gt track` existing to repair it.
- [git-branchless](https://github.com/arxanas/git-branchless) — a local event
  log of rewrites, which is durable locally but does not survive a
  server-side squash.
- [Gerrit Change-Id](https://gerrit-review.googlesource.com/Documentation/user-changeid.html)
  and [Jujutsu change IDs](http://docs.jj-vcs.dev/latest/glossary/) — the
  strongest alternative, rejected in `Decision log`: a durable identity
  carried in the object graph rather than derived from its shape.

Agent skills to load:

- `execplans` — for maintaining this document.
- `python-router`, then `python-testing`, `hypothesis`, and
  `python-types-and-apis` as the work reaches each area.
- `python-errors-and-logging` — for the `_die` and exit-code conventions.
- `codegraph-mcp` — for structural questions about the existing modules.
- `codescene-cli` — for `cs check` and `cs delta` before committing.
- `en-gb-oxendict` — for all prose.
- `commit-message` — for every commit.
- `pr-creation` and `comenq-coderabbit` — for the pull request and the review
  loop.
- `weave-git-merge` — this repository runs Weave as a merge driver for
  `*.py`; read its output carefully and recompile after any replayed commit,
  because a "clean" result is not proof of a correct one.

## Revision note

2026-09-14, revision 2. The first draft was reviewed by a six-lens expert
panel before delivery. What changed, and why:

- The evidence model gained a third tier. The first draft split evidence into
  heuristic and non-heuristic, which classified fork-point as establishing
  even though `Risks` rated fork-point surprise as the design's
  highest-likelihood hazard. `ATTESTED`, `DERIVED`, and `INFERRED` now match
  the rule to the risk, with a corroboration requirement for derived
  evidence (INV-2b).
- The result became a discriminated union and `UNSUPPORTED` was deleted. The
  first draft promised heuristics would be "structurally incapable" of
  establishing a boundary and then implemented that as a frozen set and an
  `if`; `Established.support` now cannot hold an inferred candidate by type.
  `UNSUPPORTED` went because the draft never said when to choose it.
- The Git port split in two. Read-only queries and ref writing are now
  separate protocols in separate modules, so "read-only by default" is
  enforced by the writing object not existing rather than by a test
  generator's domain.
- The content comparison was wrong and is fixed. A squash is an N-to-1
  relationship, so the first draft's per-commit `patch_identifiers` would
  only ever have matched a single-commit parent. It is now a cumulative
  comparison, with a cheap tree pass first.
- All eight gates gained a specified decision procedure, and INV-4 gained a
  per-gate obligation to have a test in which that gate alone fails against a
  real repository — because a gate implemented as an unconditional `PASSED`
  would otherwise pass every truth table in the plan.
- A fourth exit code was added for indeterminate, five undefined types were
  defined, `--op-id` gained a validation rule and a place in INV-1's domain,
  the JSON output gained a versioned envelope and a rule to emit on every
  exit code, and the receipt and shared-record formats gained versions, a
  reconciliation rule, and a grammar.
- Every unbounded operation gained a bound: `--limit`, `--heuristic-window`,
  a reflog limit, a 32-candidate cap, render truncation, an HTTP timeout, and
  a network budget.
- INV-1 and INV-6's repository-building halves became parameterized matrices,
  because Hypothesis's 200 ms default deadline under a 30-second pytest
  timeout would have turned the first real counterexample into a flake.
- Cassettes are now recorded, never authored;
  `docs/developers-guide.md:557-558` forbids hand-editing a recording, and
  the first draft proposed exactly that.
- Observability was added; the first draft omitted it entirely from a package
  where every other command records.
- The output gained full object IDs, a backup ref, the target it was computed
  against, and the gate table — because the catastrophic failure mode is a
  confident wrong answer that the user cannot undo or re-check.
- The milestones were resequenced: the specification is written first, the
  hardest fixture second, and the receipt arrives before the GitHub surface.
  EP-M5 is a deliberate release boundary. EP-M8 was added for `git plonk`
  tombstones, because `git plonk --hard` destroys the very evidence this
  command depends on.

No implementation work has begun. The plan awaits approval before Stage B.
