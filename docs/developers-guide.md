# Developer guide

This guide records internal module boundaries and local tooling conventions for
contributors working on `git-donkey`.

## Spelling policy

Run `make spelling` to enforce en-GB-oxendict prose spelling. The generated
`typos.toml` starts from the shared estate dictionary, refreshes its untracked
local cache only when the authority is newer, and then applies the narrow
repository policy in `typos.local.toml`. Edit the local policy and regenerate
the configuration rather than changing generated entries by hand.

## git-donkey workflow

`git_donkey.donkey.run_git_donkey()` is the workflow function behind the
`git donkey` console script. The [users' guide](users-guide.md) documents the
command from the outside, and the
[default-base and pull-mode design](default-base-and-pull-modes.md) records the
behavioural contract. This section describes the pipeline and the invariants a
contributor must preserve when editing the workflow.

The module split mirrors `git-fafo` and `git-plonk`: `git_donkey.cli` owns
Cyclopts parsing, `git_donkey.donkey` owns the workflow, and
`git_donkey.donkey_worktrees` and `git_donkey.templates` own the worktree and
overlay mechanics the workflow composes.

The CLI wrapper maps `branch_name`, an optional `origin_branch`, `--no-pull`,
and the fields of `_PullOptions` onto the workflow call, then raises
`SystemExit` with the returned code.

### Workflow pipeline

`run_git_donkey()` performs its steps in a fixed order:

1. `_pull_mode()` validates the pull flags.
2. `_load_donkey_context()` discovers the repository, resolves the principal
   remote, fetches it, and builds a `_DonkeyContext` holding the home
   repository, remote name, branch-to-worktree map, and worktrees root.
3. The base is resolved with `_fetch_remote_default_ref()` when no base was
   supplied, or `choose_base_branch()` when one was.
4. `_maybe_update_base_branch()` derives the local branch name, then prompts
   for and performs an opted-in update.
5. The target path is derived under the worktrees root, and
   `_create_worktree()` delegates to `git_donkey.donkey_worktrees`.
6. `_apply_template_overlay()` copies template files into the new worktree.
7. Success prints the worktree path and returns 0.

Validation runs before repository discovery, so conflicting options cannot
touch the repository. Base resolution and any opted-in update complete before
the worktree is created. Failures route through `helpers._die()`, which writes a
`git-donkey:`-prefixed message to stderr and raises `SystemExit`: conflicting
pull flags and precondition failures exit 2, while fetch, pull, worktree, and
filesystem failures exit 1. `_apply_template_overlay()` is the one step that
reports failure by returning `False`; `run_git_donkey()` then returns 1 after
the helper prints the reason. A missing overlay, or a repository whose template
directory cannot be selected, is not a failure.

### Pull options and modes

`_PullOptions` is a frozen, slotted dataclass with the mutually exclusive
`pull_rebase` and `pull_ff` booleans, both `False` by default.
`_DEFAULT_PULL_OPTIONS` is a module-level instance used as the default argument
of both `run_git_donkey()` and the CLI wrapper, so the two entry points share
one immutable default rather than declaring their own.

`_PullMode` is the `Literal["--rebase", "--ff-only"]` of the supported
strategies, with `None` meaning no update. `_pull_mode(options, *, no_pull)`
maps the flags onto that type and rejects conflicting combinations before any
repository discovery or mutation, exiting 2 with the `git-donkey:` prefix.
`no_pull` participates in the same mutual-exclusion check, so it stays valid on
its own and conflicts with either pull flag.

### Pull invariants

- No update happens without an explicit mode: `_pull_mode()` returns `None`
  unless `--pull-rebase` or `--pull-ff` is enabled, and
  `_maybe_update_base_branch()` returns immediately for `None`.
- An update runs only inside the worktree that holds the selected local base.
  `_update_base_branch_in_worktree()` looks the branch up in the context's
  branch-to-worktree map and exits 1 rather than pulling into an unrelated
  checkout when no worktree holds it.
- The behind count is a query. `_base_branch_behind_count()` must not create
  a tracking branch to measure a base: that would leave a branch no worktree
  holds and that this command cannot pull into. A base with no local branch has
  nothing to update and counts as zero behind.
- In the workflow, the prompt is the only path to
  `_update_base_branch_in_worktree()`. `_maybe_update_base_branch()` prompts
  through `helpers._prompt_yes_no()` and skips the update when the answer is no
  or the terminal is non-interactive.

### Base resolution

Explicit bases are resolved by `choose_base_branch()`, which returns the
argument unchanged except for `.`, which selects the branch checked out in the
calling directory. That branch was captured during context loading by
`helpers._get_checked_out_branch_name()`, so a detached HEAD fails before
resolution.

Implicit discovery is deliberately a command rather than a query, and is shared
with `git plonk` through `git_donkey.remote_default`. That module owns
principal remote selection (`principal_remote()`), the pure
`advertised_default_branch()` parser for `ls_remote --symref` output,
`discover_default_branch()`, and `fetch_default_branch_ref()`, which fetches
the named branch into `refs/remotes/{remote}/{branch}`.
`git_donkey.donkey._fetch_remote_default_ref()` composes discovery with the
fetch for base selection. For completion history, `git_donkey.plonk` splits the
same resolution into a query and a command, because a query must not fetch:
`_advertised_trunk()` selects the principal remote and reads the default name
it advertises, while `_fetch_canonical_trunk_ref()` composes that query with
`fetch_default_branch_ref()` and returns the remote-tracking ref. The two
commands therefore cannot disagree about which branch is trunk. Fetching
explicitly matters because a narrow fetch configuration may omit the default
branch, and a stale local `remote/HEAD` alias is not trusted.

### Adding a pull mode

1. Add a boolean field to `_PullOptions`.
2. Extend the `_PullMode` literal with the new strategy's flag string.
3. Map the field in `_pull_mode()`, adding it to the mutual-exclusion guard so
   the check stays exhaustive.
4. Extend `_pull_in_worktree()` with the matching `git pull` invocation.

The CLI wrapper passes `_PullOptions` through Cyclopts, which projects the
dataclass fields onto the flags `--pull-rebase` and `--pull-ff` today. A new
field therefore changes the command-line surface as well as the workflow.

### Workflow observability

The workflow reports what it did through `git_donkey.observability`, a small
adapter with a no-op default. A record is an `Observation`: an `operation`, an
`outcome`, and the labels that step reports, drawn from the fixed vocabularies
(`pull_mode`, `base_kind`, `error_kind`, `mode`, `skip_reason`). `mode` is the
git-plonk cleanup mode (`default`, `soft`, or `hard`) and `skip_reason` is why
the preflight left a worktree in place (`dirty` or `unavailable`). A removal
Git refuses leaves the worktree in place too; the run's result reports it with
the `removal_failed` skip reason, while its record is the `worktree_removal`
failure.

- `pull_mode_selection`: `not_requested`, `selected`, `rejected`.
- `remote_default_discovery`: `success`, or `failure` with
  `git_command_error` or `missing_advertised_default`.
- `default_branch_fetch`: `success`, or `failure` with `git_command_error`.
- `base_update`: `not_requested`, `not_behind`, `declined`, `started`,
  `success`, or `failure` with `git_command_error` or `base_not_in_worktree`.
- `worktree_creation`: `started`, `success`, or `failure`.
- `stack_record_write`: `started`, `success`, or `failure` with
  `stack_record_conflict` when the branch already had a record or the anchor
  ref could not be created.
- `template_overlay`: `unavailable` (with `selection_error` when the template
  directory cannot be selected), `started`, `success`, or `failure` with
  `os_error`.
- `comparison_fetch`: `not_requested` when `--no-fetch` is used or the
  comparison ref is a local ref owned by no remote, `success`, or `failure` with
  `git_command_error`.
- `comparison`: `found`, `empty`, `unavailable` when no upstream is configured
  and no explicit ref was supplied, or `failure` with `git_command_error`.
- `worktree_preflight`: `success`, or `skipped` with `skip_reason` `dirty` or
  `unavailable`; records `mode`.
- `worktree_removal`: `success`, or `failure` with `git_command_error`;
  records `mode`.
- `branch_deletion`: `success`, or `failure` with `git_command_error`;
  records `mode`.

Remote default discovery, the default-branch fetch, pull execution, worktree
creation, the stack-record write, the comparison fetch, the comparison, and the
cleanup boundaries (worktree preflight, worktree removal, and branch deletion)
are also timed; a span reports its operation name and duration only.

Every attribute comes from a fixed vocabulary, so records stay aggregatable.
Branch names, filesystem paths, remote URLs, Git output, exception text, and
template directory names are never recorded, because those values have
unbounded cardinality or disclose local information. The cleanup records keep
that guarantee: a skip reason is a bounded label, never a message.

The bounded vocabulary applies to `Observation` records recorded through the
`Recorder`; the operational log records described under
[Operational logging](#operational-logging) deliberately retain diagnostic
values such as `ref` and `remote`, because branch and remote names have
unbounded cardinality. This distinction is intentional and documented, not an
oversight.

Records are not exported. `NullRecorder` is the default and discards them; the
module starts no process and opens no connection. Installing `LoggingRecorder`
routes records through the structured `extra` convention described under
[Operational logging](#operational-logging) instead, and changes nothing else
about how the command behaves.

The project has no metrics backend, which is the same policy the remote
adoption classifier records: bounded reason values are the integration point
for future metrics rather than an ad hoc counter implementation. Here that
integration point is `Recorder` itself, so a metrics backend implements the
protocol and receives the bounded `operation` and `outcome` vocabulary plus
span durations; `comparison_fetch` and `comparison` can then be counted and
timed without changing the workflow.

## Machine-readable output

`git wheresat --json` prints one JSON object on **every** exit status, `2` and
`3` included, so a consumer never has to read prose to find out what happened
and never receives output it cannot parse. The object carries a `schema` string
— currently `git-wheresat/1` — and a `verdict` of `established`, `unresolved`,
`indeterminate`, or `error`. The first three label an assessment and are read
from the same table the run's observation is labelled from, so the word a
report prints and the word a recorder stores cannot drift apart; `error` is
printed by a run that collected no evidence at all.

These are the conventions for any command here that grows machine-readable
output, not rules about this one command's keys:

- A key may be added in a later minor revision. A key is never removed or
  retyped without incrementing the schema string, so a consumer reading a key
  under a schema it knows can keep reading it.
- Every key of the envelope is present on every path, with a key that holds no
  value written as `null` rather than omitted. A list is emitted when it is
  empty too — a consumer never has to tell a missing key from a run with
  nothing to say.
- The envelope is spelled out key by key rather than produced by
  `dataclasses.asdict`. Renaming a field of an internal value is then a private
  refactor, and the wire format changes only when a key is deliberately added
  or retyped.
- When `--json` is set, a failure is not routed through `helpers._die`, which
  prints prose; the run renders the error envelope instead. The envelope goes
  to standard output on every path, including this one, because a consumer
  reading the stream is reading one document and not a mixture of two.

`git_donkey.wheresat_report` owns both renderers, and neither reads anything:
the text report and the envelope are projections of one assessment and one
request, so they cannot disagree about what a run found, and a snapshot test
can pin every forensic path without a repository.

## git-fafo module boundaries

`git-fafo` is split across three modules, so infrastructure details stay out of
the orchestration path:

- `git_donkey.fafo` owns input validation, scaffold selection, local Git
  initialization, and push orchestration.
- `git_donkey.fafo_github` owns GitHub token discovery, OAuth device-flow
  fallback, repository creation, duplicate-repository detection, and adoption
  confirmation. The credentials file itself — where it lives and the shape of
  what it holds — belongs to `git_donkey.github_credentials`, which this module
  and `git wheresat` both read. Two readers of one file is the arrangement in
  which a second copy of the path becomes a second opinion, so neither command
  spells it for itself; the shared reader inverts the direction instead, since
  it is `git fafo` that writes the file and `git wheresat` that only reads it,
  and a `git wheresat` run never prompts for a token.
- `git_donkey.fafo_adoption` owns existing-remote classification. It accepts
  only remotes with no refs or one branch containing a single empty initial
  commit.

The command-line interface obtains the GitHub token before calling
`run_git_fafo()`. The workflow function therefore receives an explicit `token`
argument and can focus on validated workflow inputs rather than environment
variables, credential files, or OAuth prompts.

## git-plonk module boundaries

`git-plonk` is split across pure policy and selection, shared records, summary
rendering, CLI parsing, and infrastructure mutation:

- `git_donkey.cli` exposes the `git-plonk` console script and maps `--soft`,
  `--hard`, and `--dry-run` to `git_donkey.plonk.run_git_plonk()`.
- `git_donkey.plonk_policy` owns branch-name and commit-message policy. It maps
  issue branches such as `issue-123-title` to `(#123)`, maps roadmap branches
  such as `road-1-2-3a-4-title` to `(road.1.2.3a.4)`, and selects candidates
  whose markers appear in history. It must stay free of GitPython, filesystem,
  and process mutation.
- `git_donkey.plonk_records` owns the records and vocabulary the workflow
  speaks: the cleanup modes, the skip reasons, the candidate and result records
  (`_PlonkCandidate`, `_SkippedWorktree`, `_PlonkResult`, `_PlonkContext`,
  `_SoftPlonkContext`), and the maps that translate modes and skip reasons into
  the bounded observability labels. It holds values only: no Git access and no
  filesystem access.
- `git_donkey.plonk_selection` turns parsed `git worktree list --porcelain`
  stanzas into the git-donkey worktrees `git plonk` may clean, and into
  completed candidates. It is pure: it reads stanza values and path objects,
  never Git state or the filesystem, and it contains the only
  branch-name-to-candidate selection rule.
- `git_donkey.plonk_summary` holds the summary rendering: `_render_summary()`
  and its section helpers take a finished `_PlonkResult` and return text,
  reading no Git state and touching no filesystem. `_render_summary()` is what
  `run_git_plonk()` prints.
- `git_donkey.plonk` keeps orchestration (`run_git_plonk()`), the GitPython
  adapter, worktree removal and branch deletion, and dry-run planning.
- `git_donkey.remote_default` owns the principal-remote and default-branch
  discovery both commands use, so completion history and base selection cannot
  diverge.

`git_donkey.plonk_policy.completed_candidates` is generic over its candidate
type: it accepts any iterable whose items expose a read-only `marker` property
returning `str`, returns the matching candidates unchanged, and never mutates
the candidates it receives. `git_donkey.plonk` therefore passes its worktree
candidates directly.

The plonk workflow deliberately reads completion history from the canonical
trunk ref resolved by `remote_default`. This allows `git plonk` to be invoked
from a linked topic worktree while still using the trunk history that contains
issue or roadmap merge markers.

Default and hard modes only consider linked worktrees under
`../{repo}.worktrees` and only remove worktrees whose branch-derived completion
marker is present in canonical trunk history. Before removing one, they ask
`_GitWorktreeAdapter.skip_reason()` whether Git would discard it unprompted:
worktrees with modified, staged, or untracked files, and worktrees whose
directory is gone, are skipped and reported as `_SkippedWorktree` entries while
the sweep continues. Removal never passes `--force`. Hard mode deletes local
branches after a successful unforced removal, so a skipped worktree keeps its
branch; it does not delete remote branches. Soft mode uses the same git-donkey
worktree discovery but only removes conventional generated directories such as
`target`, `node_modules`, `.venv`, and cache directories.

A local branch Git refuses to delete is reported rather than fatal. The
worktree counts as removed, the branch is neither reported as removed nor
reported as skipped, and the failure gets its own `Failed branch deletions:`
summary section. The sweep continues with later candidates, and
`run_git_plonk()` returns exit status 1 for a run whose branch deletion failed;
a run that only skipped worktrees still returns 0.

The decision table, the skip vocabulary, and the reasoning behind reporting
skips rather than forcing removal are recorded in the
[plonk cleanup policy](plonk-cleanup-policy.md).

`pytest-bdd` and `syrupy` are development dependencies for this command.
`pytest-bdd` covers user workflows against real temporary Git repositories, and
`syrupy` pins stable summary rendering. Hypothesis checks marker-shape
invariants in the pure policy layer.

## git-wheresat module boundaries

`git-wheresat` answers one question — which commit is a branch's exclusive
replay boundary — and its implementation is split so that each part of the
answer lives where it can be read without doing anything else. The value
vocabulary, the failure vocabulary, the read-only Git port, the writing
surface, the evidence rungs, the parent-head ladder, the deep comparison, the
graph facts, the gates, the policy, and the two renderers are separate modules;
`git_donkey.wheresat` is the only one that decides when each is called.

- `git_donkey.wheresat_records` owns the value vocabulary every other
  `wheresat` module exchanges: the evidence kinds and their tiers, the
  candidate types a rung returns, `CommitRange` and `range_key()`, the eight
  `GateName` values with `GateOutcome` and `GateResult`, `GraphFacts`,
  `WorktreeState` and `GitOperation`, the three assessment arms, and the
  `EXIT_CODES` map with `EXIT_USAGE`. It holds values only — no Git,
  filesystem, network, or process — so a forensic path such as a superseded
  record, a gate that could not be answered, or a stored tip that no longer
  exists is a value a test builds without a repository. Its only intra-package
  import is `stack_records`, for the pull request identity the shared record
  carries.
- `git_donkey.wheresat_errors` is the failure vocabulary, and holds nothing
  that reads anything: `WheresatGraphError` and its `ShallowHistoryError`
  subclass, `WheresatGitHubError`, `WheresatUsageError` and its
  `WheresatCredentialError` subclass, and `_reported()`, which turns one failed
  command into the single line a message can carry. The graph failures live
  below both halves of the read-only port rather than in the reader that
  history questions happen to be asked through, so the refusal a shallow clone
  produces is one class whichever half asked the question.
- `git_donkey.wheresat_graph` is the read-only Git port. It owns the
  `WheresatGraph` protocol, the `GitWheresatGraph` adapter over GitPython, and
  `WheresatGraphError` with its `ShallowHistoryError` subclass. Nothing here
  can write: it resolves revisions, asks ancestry questions, lists ranges,
  compares trees and patches, and reads what the worktree holding the child
  branch is in the middle of. A worktree stopped mid-rebase is left behind by
  its branch rather than holding it, so the port reads both the worktree
  listing and the operation's own state directory before naming the worktree a
  branch belongs to.
- `git_donkey.wheresat_worktrees` is the other half of that port: it reads
  whether the worktree holding a branch is in a state a replay could be run in.
  It shares nothing with the history reader but the failure vocabulary, and is
  kept apart from it because its subject is the working tree rather than the
  commit graph — a branch read with `--branch` need not be checked out
  anywhere, and a worktree can be stopped in an operation that has detached
  from it. The worktree Git lists is read through its own `.git` entry and its
  own `status`, so the answer is about the worktree holding the branch rather
  than about the repository the run was started in.
- `git_donkey.wheresat_refs` is the only object that writes, and a run
  constructs it only when it has to write. It owns the three evidence
  namespaces — `refs/wheresat/op/<op-id>/` for one run's own fetches,
  `refs/wheresat/parent-head/<owner>/<repo>/<n>`, and
  `refs/wheresat/boundary/<branch>` for a boundary no other ref reaches — plus
  `validate_op_id()`, the `WheresatRefWriter` protocol, and the
  `GitWheresatRefWriter` adapter. `release()` deletes the per-run namespace and
  nothing else, and the run calls it from a `finally` block; the namespace is
  never swept wholesale, because refs live in the common ref store and every
  worktree of a checkout shares `refs/wheresat/`.
- `git_donkey.wheresat_writes` is the command's complete writing surface: the
  three things a run can change — the parent head it fetched into the durable
  cache ref, the ref that keeps a reported boundary from being collected, and
  the shared stack record `--record` refreshes — are methods here rather than
  branches of the workflow, so "what can this run change?" has one file for an
  answer. Constructing this module's value object holds a repository and
  nothing that writes to it, and the object that does the writing is built
  inside the method that needs it, so a run that asks for none of the three
  never has one to reach for.
- `git_donkey.wheresat_remotes` reads the checkout's configuration to answer
  two questions: which GitHub repository its remotes name, and which remote
  holds a given repository. A remote may carry more than one URL and a URL may
  name no GitHub repository at all — another host, a local path, a bundle, an
  `insteadOf` short form — and such a URL is answered with nothing rather than
  read as a near miss, because the callers are looking for the one remote that
  holds a particular repository and a guess would fetch a parent's head from a
  stranger. The configuration is read directly rather than through
  `git remote get-url --all`, which also applies `url.<base>.insteadOf`
  rewrites: a remapped remote therefore names no repository, and the run
  reports the head as unfetchable rather than turning the user's configuration
  into an assumption about where the evidence came from.
- `git_donkey.wheresat_payload` reads GitHub's decoded bodies. It is pure — no
  session, no URL, no request — and split out so that the module which speaks
  HTTP is about requests and their faults and this one is about the shape of
  what came back. Its strict and lenient readers are separate functions on
  purpose: a field that is absent is nothing, and a body that is not the shape
  its endpoint promises is a question that went unanswered, and one tolerant
  helper would read those two as the same thing.
- `git_donkey.wheresat_github` is the `WheresatGitHub` port and its `github3.py`
  adapter, and it asks the forge exactly four questions: what a pull request
  merged as, what its body says, whether GitHub records it in a stack, and
  which pull requests a bounded set of commits belongs to. Every question goes
  through one primitive, so an answer that is not `200` becomes a
  `WheresatGitHubError` there and nowhere else and a status the module has
  never seen cannot reach a caller as data. That is INV-5 at this boundary: a
  `404` from a credential that cannot see a private repository is a question
  GitHub could not answer, not a parent that is not there. `github3.py` 4.0.1
  does not model the `stack` field, so it is read through
  `pull_request.as_dict()`, which returns the raw payload, and the
  `GET /repos/{owner}/{repo}/stacks` call is made through the library's own
  `requests.Session` so that `vcrpy` still intercepts it. The association
  search is bounded by commits and by wall clock, and reports truncation rather
  than returning the part it saw as the whole answer.
- `git_donkey.wheresat_shared_record` owns the prose form a pull request body
  may carry in place of a local record: a `Stack parent:` line and a
  `Replay boundary (exclusive):` line, which travel to a clone the record never
  reached. It parses that block and renders it back, and it is pure and
  resolves nothing — a value outside the grammar, half a record, and a body
  that supports several readings are each reported as they were found, because
  what it returns is a claim the run then validates rather than an instruction
  it obeys. Lines inside a fenced block are quoted material and are skipped,
  while leading Markdown decoration is removed, so a record a body bullets or
  quotes is a claim like any other.
- `git_donkey.wheresat_ladder` is the ladder's vocabulary rather than its
  policy: what a rung answers with, what it is handed to read through, and the
  two recorders that make an answer and a fault. Every rung answers the same
  way whichever question it put, and the module that asks them reads as the
  order and the refusals it is.
- `git_donkey.wheresat_parents` identifies the parent pull request the child is
  stacked on, by asking five rungs in order: an explicit `--parent`, the pull
  request the child's own stack record names, the stack GitHub records, the
  shared record the child's pull request body carries, and last the pull
  requests associated with the child's commits. Each rung is a weaker statement
  than the one before it, and a run that answered a stronger question has no
  business asking a weaker one. There is no forge client here — the ladder asks
  the port questions — so one walk serves the live API and a recorded one, and
  a run that was told not to touch the network has nothing to open. A question
  that goes unanswered is a fault and stops the ladder rather than letting the
  next rung's weaker answer be presented as the answer to the question that
  failed, which is the reading ADR-005 forbids. The body's claim is read here
  and handed on with the identification, because two phases read that record —
  this ladder, for a parent it may name, and the collection phase, for the
  boundary — and neither may read a different claim than the other.
- `git_donkey.wheresat_heads` answers the question that follows that ladder:
  which commit the parent's tip was, and which ref says so. Its three rungs are
  the head the run already fetched, the tombstone `git plonk` wrote for the
  parent branch, and that branch's remote-tracking ref — each a weaker
  statement than the one before it. A rung that finds nothing falls through to
  the next, and a rung that hits a fault stops the ladder and returns the
  reason, because "no tombstone" and "the tombstone would not open" are
  different answers (INV-5). The tombstone proposes no boundary of its own:
  where the head was is a different question from where the child was cut.
- `git_donkey.wheresat_collect` asks the evidence rungs in the procedure's
  order: the stack record, the shared record the child's own pull request body
  carries, the head the run fetched for the parent, the merge base, the fork
  point, and the two deep comparisons behind `--deep`. A rung returns the
  candidates it found, or a fault when it could not answer at all, because
  "there is no evidence here" and "this question went unanswered" are different
  answers and only one of them is a refusal. The shared record is read by the
  ladder rather than here, so this rung asks no forge, and it is silent for a
  claim the ladder already refused — one unusable claim is reported once, where
  it was found. The parent head the gates ask about is the one `wheresat_heads`
  recovered.
- `git_donkey.wheresat_deep` is the content-comparison scan behind `--deep`,
  and the one rung that answers a question of its own rather than reading
  something recorded: has the work a child commit carries already landed on the
  target, and as which commit? It runs two passes, cheapest first — a
  whole-tree comparison, then a cumulative-patch comparison for the child
  commits the first left unmatched — and the window it scans is bounded, so a
  scan the window cut short is a caveat rather than a complete answer.
- `git_donkey.wheresat_facts` asks every graph question the eight gates will
  read, before any gate runs. The gates are then a pure function of a
  `GraphFacts` value, which is what lets a property test hand the policy an
  arbitrary graph. Nothing is asked speculatively: a run that recovered no
  parent head pays for no question about one.
- `git_donkey.wheresat_gates` owns the eight named gates, each with a
  specified decision procedure and three answers. `ancestry_outcome()` decides
  the "cannot tell is not no" rule once: an ancestry question Git could not
  answer never becomes `FAILED`, so no refusal can rest on a question that was
  never put.
- `git_donkey.wheresat_policy` reads gate results into a verdict, and is pure:
  no Git, filesystem, network, or process. `may_establish()` owns the
  corroboration rule, and the module owns precedence — candidates at the
  strongest tier a run found are the only ones that can serve, so a lone
  derived candidate is not outranked by four inferred ones under it, and two
  candidates left at the same tier are an ambiguity the run refuses rather than
  a tie broken by source order. A record whose attested claim gate 8 refused is
  demoted to derived evidence rather than discarded, so it can still support
  the commit it names once another source agrees with it.
- `git_donkey.wheresat_report` renders an assessment as text or as the
  versioned JSON envelope. Nothing in it reads anything: both renderers are
  projections of the assessment and the request, so they cannot disagree about
  what a run found, and a snapshot test can pin an established boundary, a
  refusal, and an environment that could not answer without a repository.
- `git_donkey.wheresat_request` is what the run was asked and how it is
  resolved: the options as the command line spells them, the two checks that
  refuse an `--op-id` that could escape its namespace and an `--expected-old`
  no record write would consult, and the resolution of the branch, the target,
  and a parent the run named into immutable object IDs. Resolution is where a
  run can fail before it has anything to report, so a name that does not
  resolve is a usage error rather than an indeterminate result.
- `git_donkey.wheresat` keeps the run itself: the exit status and the bounded
  observations. `run_git_wheresat()` resolves what the run was asked to
  something immutable, calls the modules above in the procedure's order, and
  returns the exit code; `git_donkey.cli` exposes it as the `git-wheresat`
  console script, from which Git discovers `git wheresat`.

The report is deliberately wider than the verdict. A boundary that no durable
ref reaches is retained under `refs/wheresat/boundary/<branch>` before it is
reported, so a later `git gc` cannot take away an answer the report has already
given, and the report warns when the worktree holding the branch would not
accept the replay it prints. A warning is not evidence: an unreadable worktree
is warned about rather than treated as a fault, because a run that warned about
nothing would be read as a run with nothing to warn about, and a warning
changes neither the verdict nor the exit status.

The JSON envelope is spelled out key by key rather than produced by
`dataclasses.asdict`. Renaming a field of the assessment is then a private
refactor, and the wire format changes only when a key is deliberately added or
retyped, which the schema string in the envelope records; `warnings` is always
present, including when it is empty, so a consumer never has to tell a missing
key from a run with nothing to say.

The read-only guarantee is measured rather than asserted.
`tests/integration/test_wheresat_read_only.py` runs an explicit matrix of
argument vectors — the default run, each flag that only reads, a named branch,
and the refusal and usage-error paths — against a real repository and compares
refs, `HEAD`, the index, the working tree, `FETCH_HEAD`, the stash, and local
configuration before and after. The fingerprint is exercised against a changed
value so that an equality assertion cannot pass by measuring nothing, and
evidence a run is entitled to write is classified as allowed rather than as a
difference. `--record` is the one flag that writes a record, so it is measured
by `tests/integration/test_wheresat_record.py` instead, which also pins that a
run asked for no write builds no writer at all, and the run asked for one
builds exactly one. `tests/integration/test_wheresat_end_to_end.py` runs the
three commands in the order the feature exists for: `git donkey` cuts a child
and records the boundary, `git plonk --hard` sweeps the merged parent and
leaves a tombstone, and `git wheresat` has to answer for the child from what
survived.

`syrupy` pins the text report and the JSON envelope, and Hypothesis drives the
pure policy over arbitrary gate corpora: `tests/unit/test_wheresat_report.py`,
`tests/unit/test_wheresat_policy.py`, and
`tests/unit/test_wheresat_properties.py`. `docs/man/git-wheresat.rst` is the
man page source for the command, built and installed like the others and
covered by `tests/unit/test_manpage_sources.py`.

## git-incoming and git-outgoing module boundaries

`git-incoming` and `git-outgoing` answer the two questions a developer asks
before syncing with a shared branch: which commits would arrive on a pull, and
which would leave on a push. The implementation is split between pure
comparison policy, a Git-facing workflow, and CLI delegation, so comparison
rules stay testable without GitPython while the workflow owns Git work and
user-visible behaviour:

- `git_donkey.incoming_outgoing_policy` owns the pure comparison decisions.
  `remote_name_for_ref()` returns the configured remote that owns a comparison
  ref, handling both `origin/main` and canonical `refs/remotes/origin/main`
  forms, and `comparison_range()` returns the include/exclude ref pair for a
  direction. It contains no GitPython, filesystem, or process mutation and
  mirrors the `plonk_policy` precedent.
- `git_donkey.incoming_outgoing` owns Git work and user-visible behaviour.
  Ref resolution (`_resolve_comparison_ref`) and commit lookup
  (`_commits_unique_to`) are queries: they return data, a ref string or
  `git log` output, without printing, fetching, or otherwise changing state.
  `_run_comparison` is the command boundary: it owns repository discovery,
  comparison-ref resolution, rendering to stdout or stderr, and exit-code
  mapping. Its `_fetch_comparison_remote` helper owns the optional fetch: it
  honours `--no-fetch`, fetches the remote owning the comparison ref, and
  reports whether the comparison may proceed.

`_run_comparison` accepts a frozen `_ComparisonRequest` value object carrying
the prefix, direction, optional ref, and fetch flag, plus an optional injected
adapter. The `_ComparisonAdapter` protocol describes the Git surface the
queries need: `upstream_ref() -> str | None`, `remote_names()`,
`fetch_remote(remote)`, and the inherited `log(*args) -> str`.
`_GitPythonComparison` implements it over GitPython and delegates fetching to
the shared `helpers._fetch_remote`. Tests drive the workflow with fakes that
satisfy this protocol, so comparison behaviour is exercised without a real
repository.

`git_donkey.cli` is the console-script boundary. It defines Cyclopts `App`
instances for `git incoming` and `git outgoing`, and delegates through a
`_ComparisonRunner` protocol and the shared `_run_incoming_outgoing_cli` helper
to the public runners `run_git_incoming(ref=None, *, fetch=True)` and
`run_git_outgoing(ref=None, *, fetch=True)`. `pyproject.toml` registers four
console scripts pointing at `git_donkey.cli`: `git-incoming`, the `git-in`
alias, `git-outgoing`, and the `git-out` alias. Git discovers subcommands by
executable name, so the aliases need their own scripts rather than argument
aliases on the primary commands.

Both runners return standard process exit codes:

- `0` means matching commits were found and printed.
- `1` means the comparison succeeded but found no matching commits; both
  codes are the ones Mercurial documents.
- `2` is git-donkey's own code for a command that could not run: no upstream
  configured and no explicit ref, a configured upstream cannot be resolved, a
  failed fetch, or a failed comparison.

The shared `helpers._fetch_remote` exits `1` on failure, so the workflow
catches that `SystemExit` and remaps it to `2`; otherwise a fetch failure would
masquerade as "no changes" rather than as a failed command.

## Operational logging

`git-fafo`, `git-plonk`, and the `git incoming` and `git outgoing` workflows
log decision boundaries without logging secrets. Stable fields are provided
through `extra`, so callers can route records into structured logging later:

- `token_source` records whether credentials came from the environment, cache,
  or device flow.
- `operation` records GitHub and Git operations such as repository creation,
  local initialization, push, generated-path cleanup, worktree removal, and
  branch deletion.
- `repo_name`, `owner`, `branch`, `result`, and adoption `reason` provide
  diagnostic context for repository decisions.
- `mode`, `worktree`, `marker`, `candidate_count`, `completed_count`, and
  `removed_count` provide diagnostic context for plonk cleanup decisions. A
  skipped candidate also records `operation` of `skip_worktree` and a `reason`
  from the skip vocabulary, at `INFO` because leaving a worktree in place is a
  decision rather than a fault.
- Incoming and outgoing comparisons use `operation` (`compare` or `fetch`),
  `direction` (`incoming` or `outgoing`), `fetch_enabled`, `ref`, `remote`,
  `commit_count`, and `result` (`found`, `empty`, `unavailable`, `success`, or
  `failure`). Records are emitted at comparison start, ref resolution, fetch
  selection, fetch completion, fetch failure, and comparison completion; a
  successful fetch is confirmed at `INFO` once it returns, while failures are
  also reported with `_LOGGER.exception` (comparison failure) and
  `_LOGGER.warning` (fetch failure). An unset upstream is logged at `INFO` with
  `result` of `unavailable`, and a configured upstream that cannot be resolved
  is logged at `WARNING` with `result` of `failure`.

## Dead-code detection

`make lint` runs Skylos `4.33.2` as its final check, after the checks
enumerated under [Lint workflow](#lint-workflow). Skylos scans only
`git_donkey`, explicitly excludes `tests`, reports only dead-code findings,
does not upload results or collect provenance, and blocks local linting and
continuous integration on unexplained code. Skylos parses source with its own
runtime Abstract Syntax Tree (AST), so its command-only CLI macro pins Python
3.14. The pin prevents newer Python syntax from producing phantom dead-code
findings. The separate `$(SKYLOS)` macro adds scan-only options such as
`--config-file` for the lint target.

Treat every finding as dead code until its caller is verified. Remove genuine
dead code. For a framework callback, protocol implementation, or another
implicit runtime caller, first add a narrow typed entry-point rule in
`[tool.skylos.dead_code]` with the fully qualified symbol and a caller-specific
reason. Only when no entry-point rule can model a verified false positive, run:

```shell
make skylos-allow SYMBOL=symbol REASON="Verified runtime caller"
```

The helper requires both values to contain non-whitespace text, invokes
`skylos whitelist` before its reason, and records the symbol and explanation in
`[tool.skylos.whitelist]` in `pyproject.toml`. It rejects a missing or
whitespace-only value with exit status 2. Use `SYMBOL`, not `NAME`: Windows
Subsystem for Linux (WSL) injects `NAME` with the hostname. Do not add
speculative, bulk, or unexplained allow-list entries. The helper holds the
ignored repository-local `.skylos-whitelist.lock` with `flock` while Skylos
performs its read-modify-write update, preventing concurrent contributors from
losing a verified exception.

`tests/unit/test_skylos_lint_contract.py` parses the Makefile with Makeutil and
checks the Skylos and continuous-integration boundaries. `make test` verifies
that `makeutil` is present before invoking the suite. Before running the full
test suite locally, install the same pinned parser used by CI:

```shell
rustup toolchain install nightly-2026-05-28 --profile minimal
RUSTFLAGS="-Zpolonius=next" cargo +nightly-2026-05-28 install \
  --git https://github.com/leynos/makeutil \
  --rev 29fc5a1634ffbaa18a773eed9dff1b2838a45d9c \
  --locked --force makeutil
make test
```

## Tool pinning

The `Makefile` pins Ruff with `RUFF_VERSION` and ty with `TY_VERSION`, and
invokes each through the `RUFF` and `TY` variables, which run
`uv tool run ruff@$(RUFF_VERSION)` and `uv tool run ty@$(TY_VERSION)`. Every
invocation therefore uses the pinned version regardless of which `ruff` or
`ty`, if any, is on `PATH`, so local runs cannot silently diverge from
continuous integration.

Pin both deliberately. Rule sets differ between Ruff releases and diagnostics
differ between ty releases, so an unpinned tool reports problems in one
environment that never appear in the other.

`TY_VERSION` is the sole ty version declaration: continuous integration runs
`make typecheck` and installs no separate ty. Ruff is also installed as a
development dependency and as a continuous integration tool, so keep
`RUFF_VERSION`, the `ruff==` entry in `pyproject.toml`, and the
`uv tool install ruff==` step in `.github/workflows/ci.yml` in sync.

The `typecheck` target adds `scripts` to the type checker's module search path,
because that directory holds PEP 723 single-file helpers that import each other
by module name. Keep the path scoped to the target rather than changing
application import paths.

## Lint workflow

`make lint` runs seven checks in order. The `lint` target invokes them as:

```make
$(RUFF) check
$(UV_ENV) uv run interrogate --fail-under 100 git_donkey
pyscn check git_donkey tests --skip-clones
$(PYLINT_BUILTIN) $(PYLINT_TARGETS)
$(PYLINT_DF12) $(PYLINT_TARGETS)
$(UV_ENV) uv run ambrleaks tests
$(SKYLOS) $(SKYLOS_PRODUCTION_TARGETS) --exclude $(SKYLOS_EXCLUDE_FOLDERS) \
	--category dead_code --gate --format concise \
	--no-upload --no-provenance --no-grep-verify
```

Taking each in turn:

- **Ruff** expands to `uv tool run ruff@$(RUFF_VERSION) check`, and is
  configured by `[tool.ruff]` in `pyproject.toml`.
- **interrogate** enforces 100% docstring coverage across the package.
- **pyscn** runs complexity and dead-code analysis. It is a `PATH` tool that
  continuous integration installs with `uv tool install pyscn`; it is not a
  development dependency.
- **The built-in Pylint pass** expands to `uv run pylint` with `-j` and the
  shared targets, and is configured by the `[tool.pylint.*]` tables in
  `pyproject.toml`.
- **The df12-python-lints Pylint pass** adds `--rcfile=.pylintrc-df12.toml`,
  and is configured by that file.
- **ambrleaks** scans syrupy `.ambr` snapshot files for unredacted values such
  as absolute paths, hex strings, UUIDs, and e-mail addresses.
- **Skylos** performs strict production dead-code detection under its own
  pinned interpreter. See [Dead-code detection](#dead-code-detection) for its
  configuration and exception policy.

The two Pylint passes share `PYLINT_TARGETS`, which defaults to
`git_donkey scripts tests`, so both always analyse the same files. It is
declared with `?=` and can be overridden on the command line. `PYLINT_JOBS` is
a tenth of the machine's cores with a floor of two, keeping the pass parallel
on small continuous integration runners while leaving headroom on large shared
machines.

Running `make lint` requires `uv` and `pyscn` on `PATH`; the Makefile's `TOOLS`
list and `ensure_tool` check fail early with a clear message if `uv` is missing.
`pyscn` is the one lint tool `uv` does not provide, so continuous integration
installs it with `uv tool install pyscn`. Everything else needs no separate
installation: Ruff and Skylos run via `uv tool run` at pinned versions, while
interrogate, both Pylint passes, and ambrleaks run via `uv run` from the
project virtual environment. The project requires Python 3.13 or newer, but
`uv` provisions a suitable CPython interpreter for the virtual environment, so
contributors need no matching system Python. No Node.js tooling is needed for
`make lint`; that belongs to the separate `markdownlint` and `nixie` targets.

### Development dependencies

The `[dependency-groups] dev` table in `pyproject.toml` provides `pylint`
(constrained to `>=4.0,<5`), `df12-python-lints` (pinned to the `v0.2.0` git
tag), `interrogate`, and `ruff`. The command `uv sync --group dev` installs
them into `.venv`, and `make build` runs exactly that.

`ambrleaks` is not a separate distribution. It is a console script entry point
of `df12-python-lints`, which is why `uv run` finds it once the development
group is installed.

The `lint` target deliberately does not depend on `build`. Continuous
integration runs `make build` as an explicit setup step, and every command in
`lint` that needs the virtual environment goes through `uv run`, which
synchronizes the project environment on demand. Continuous integration
therefore synchronizes exactly once, and `make lint` still works from a clean
checkout with no `.venv`.

### Why two Pylint passes

Pylint accepts a single configuration per invocation and a single
enable/disable message set, so two independently curated message sets cannot
share one run. Both passes start from a blanket disable and then enable an
explicit allowlist: the built-in allowlist tracks upstream Pylint messages,
while the df12 allowlist tracks the plugin's house-style checkers.

Separating them means the plugin is loaded only in the pass that needs it, and
editing one allowlist cannot silently change the other. Both passes run under
the same CPython interpreter from the project virtual environment, so they
analyse identical syntax.

## Test infrastructure

The root `conftest.py` provides GitHub API stubs shared by unit and integration
tests. Integration-specific Git repository helpers live in
`tests/integration/conftest.py`.

The same file registers two Hypothesis profiles, `default` (fifty examples) and
`nightly` (five hundred), and selects one with `HYPOTHESIS_PROFILE`. Both
disable the deadline and suppress the `too_slow` health check, because every
property here builds a repository or runs a Git command: a deadline would fail
on a slow machine rather than on a defect. A property test that pins its own
example budget, as the record lifecycle's state machine does, overrides the
profile's count on purpose — one generated step of that machine is a dozen Git
subprocesses.

`tests/git_repo_helpers.py` provides shared builders that create real
repositories: `configure_repo()`, `seed_repo()`, and
`repo_with_remote_default()`. Both the unit and integration suites use them,
because these tests pin Git's own behaviour (which remote default a repository
advertises, and what `git worktree remove` refuses) rather than a Python
double's idea of it. The builders configure a local commit identity, so tests
never read or write the runner's global Git configuration.

Behavioural scenarios live in `tests/integration/features/`, each bound by a
`test_*_bdd.py` module that names it in a single `scenarios(...)` call at the
end. Give every step a wording of its own. A `parsers.parse` pattern matches a
step name with `fullmatch`, and a `{placeholder}` matches any run of
characters, so a pattern that leaves a phrase optional also accepts the text of
the step that spells it out, capturing the extra words as part of the
placeholder instead of failing to match. Two steps that both accept one line of
Gherkin therefore do not split it tidily and the collision is silent: the
scenario runs the wrong step with a plausible-looking argument. When two steps
differ only in what is absent, word them apart — "naming no base" rather than a
shared pattern with the base omitted.

`tests/observability_helpers.py` holds the recording recorder used to assert
bounded workflow records, plus `declared_attribute_values()`, which derives the
bounded vocabulary from the `git_donkey.observability` type aliases. The root
`conftest.py` installs it through the `recording_recorder` fixture.

`tests/integration/conftest.py` also provides the `stub_commands` fixture. It
creates temporary `git` and `copier` executables that append their command-line
arguments to a log file. Scaffold workflow tests should use this fixture
instead of writing per-test command stubs.

The same module provides three cassette fixtures. `github_api_cassette` loads
an empty cassette (`interactions: []`) from `tests/integration/cassettes/`
through `vcrpy` in its `none` record mode: because no interaction is recorded,
any HTTP request raises inside the code under test instead of reaching the
network. The worktree commands reach their remote over the Git protocol, which
the temporary bare repositories stand in for, so a scenario run under the
fixture proves that `git donkey` and `git plonk` never consult the GitHub API.
`wheresat_parent_metadata_cassette` and `wheresat_rate_limited_cassette` replay
the traffic `git wheresat` reads its parent evidence from. Record a real
cassette only for a command that is meant to call the API, and never edit a
recording by hand.

Every cassette is played through one `_recorder()` helper, which filters the
credential out of each request before the interaction is written, so no
recording can carry what the traffic was recorded with: the filtered set is the
`authorization` header itself. `vcrpy`'s `filter_headers` reaches a request and
nothing else, so the three headers GitHub's API sends in its answers are
dropped from each response instead, by a `before_record_response` hook. They
name the client and the access it was granted — `x-oauth-client-id`,
`x-oauth-scopes`, and `x-accepted-oauth-scopes` — and none of them is read by
any test. Replay does not miss either kind: `vcrpy` matches an interaction on
the request's method and URL rather than on what it carried, so a run holding a
token and a run holding none replay the same recording, and a response header
cannot make an interaction unfindable.

A recording made before the response hook existed may still carry those OAuth
headers on disk, and a recording that does is left as it was recorded: the rule
above holds for every recording, and a file that needs to say less is
re-recorded rather than repaired by hand. Nothing reads them there: the hook
drops them as a recording is loaded, which is what
`tests/integration/test_wheresat_github.py` asserts, and a re-recording pass
that writes a file strips them from it, because the file is written from the
interactions the recorder holds rather than from the bytes it read.

`vcrpy` 7.0.0 ships no pytest plugin, so the root `conftest.py` declares the
`--record-mode` option itself — `none`, `once`, or `new_episodes`, defaulting to
`none` — rather than relying on one a future release might provide. The
default is what the suite runs in, and it is the guarantee rather than a
convenience: an unrecorded request raises inside the code under test instead of
leaving the machine. Recording is a deliberate pass against live traffic:

```shell
env -u GH_TOKEN GITHUB_TOKEN="$(env -u GH_TOKEN gh auth token)" \
  uv run pytest tests/integration/test_wheresat_github.py \
  --record-mode=once -q
grep -c -i '^ *authorization:' tests/integration/cassettes/wheresat_*.yaml
```

`GH_TOKEN` is unset twice on purpose. An injected `GH_TOKEN` shadows the stored
`gh` session, so `gh auth token` prints the shadowing value and not the
session's; unsetting it only for the test process would leave the substitution
reading the wrong token. The `grep` must print `0` for every file: a recording
that carries a credential is a committed secret.

A recording cannot be shown to have been replayed by counting. `play_count` is
`0` while a cassette is being written, and `Cassette.requests` returns the
interactions the recording holds rather than the requests a run made.
Provenance is therefore the record mode plus an assertion: `_asked()` in
`tests/integration/test_wheresat_github.py` names the request each answer was
read from, so a test that starts asking a different question fails as the
change it is rather than replaying a stale answer.

Re-recording the rate-limit cassette is a deliberate act with a cost. GitHub
refuses a request whose endpoint allowance the credential has spent with a
`403` and `X-RateLimit-Remaining: 0`, and that header is how a spent allowance
is told from a missing scope, which an operator answers in the opposite way.
The only honest way to record it is to spend a real allowance and ask, so it is
kept in a cassette of its own and a pass over the other recordings does not
touch it. Spend `/search/code`'s ten-request-per-minute allowance to reproduce
it: that costs a minute and affects nobody else, where the core allowance and
the anonymous one are shared with every other caller of the API.

The parent-metadata cassette carries a dependency the rate-limit one does not.
Its stack question is put to the Stacks API, which GitHub serves as a public
preview, and the reader also takes the `stack` field of the pull request
payload; either may change or be withdrawn. What the recording holds is
`microsoft/vscode` pull requests 335346 and 335345, the second and first
entries of one four-deep stack, so re-recording it needs both pull requests to
still exist, still be in that stack, and still be readable with the credential
in use. No test depends on that being true today: the suite replays the
committed cassette in the default `none` record mode, so the cases keep passing
once the live stack moves on or the preview ends, and they fail only when the
reader starts asking a question the recording cannot answer.

The behaviours the
[worktree-management skill](../skill/git-donkey-worktrees/SKILL.md) documents
are pinned by behavioural suites that build ephemeral repositories and real
`git donkey` worktrees. `tests/integration/test_git_donkey_bases_bdd.py` binds
`features/git_donkey_bases.feature`: the mandatory fetch, the equivalence of
`--no-pull` and the default, the pull-option usage error raised before any
repository access, the non-interactive prompt, `.` as a base, the principal
remote, and the absence of GitHub API calls.
`tests/integration/test_git_donkey_reuse_bdd.py` binds
`features/git_donkey_reuse.feature`, and both share the `DonkeyScenario` record
and runners in `tests/integration/donkey_helpers.py`: branch reuse and its
upstream rules, occupied-branch and existing-path conflicts, detached `HEAD`,
nested branch paths, and overlay overwrites.
`tests/integration/test_git_plonk_trunk_bdd.py` and
`test_git_plonk_markers_bdd.py` cover trunk discovery (a non-`main` default, a
missing advertisement, an unreachable remote, the fetch a dry run performs, and
the absence of GitHub API calls) and completion-marker semantics.
`tests/integration/test_git_plonk_scope_bdd.py` and
`test_git_plonk_outcomes_bdd.py` cover the path-based worktree scope, remote
branches surviving hard mode, soft mode's reach, missing directories, skipped
previews, refused branch deletions, and exit statuses.

`tests/integration/_fafo_adoption_stubs.py` builds local bare remotes with
specific histories for adoption tests. Use these helpers when adding new
existing-repository scenarios, so the tests stay focused on behaviour rather
than Git setup.

`tests/unit/test_fafo_error_messages.py` pins complete user-facing error
messages. Add new cases there when a new `git-fafo` conflict or credential
failure path is introduced.

The `git-plonk` test modules are split the same way as the production modules
they verify. `tests/integration/test_git_plonk_bdd.py` binds the scenarios in
`tests/integration/features/git_plonk.feature`: the default, soft, and hard
modes, their dry runs, the mutually exclusive `--soft --hard` usage error, and
the skip-and-report contract for completed worktrees holding a tracked
modification, a staged change, or an untracked file.
`tests/integration/test_git_plonk_trunk_history.py` proves cleanup follows the
advertised default branch's history rather than a topic worktree's own history
or a stale local `refs/remotes/origin/HEAD` alias, and never removes the
invoking worktree. `tests/integration/plonk_helpers.py` holds the repository
builders both suites compose: `PlonkScenario`, `commit_completion_marker()`,
`commit_ignore_rule()`, `create_git_donkey_worktree()`, `edit_tracked_file()`,
`stage_tracked_change()`, and the `TRACKED_FILE`, `MODIFIED_CONTENT`, and
`STAGED_CONTENT` constants. On the unit side, `tests/unit/test_plonk.py` keeps
summary rendering, `tests/unit/test_plonk_selection.py` covers selection,
`tests/unit/test_plonk_cleanup.py` the completed cleanup workflow, and
`tests/unit/test_plonk_worktree_adapter.py` the worktree adapter against real
Git; `tests/unit/test_plonk_soft_mode.py` covers the soft pass and
`tests/unit/test_cli_plonk.py` the CLI flags. The split exists because it runs
along the production boundaries each module verifies and keeps every module
below CodeScene's Low Cohesion threshold of four.

## Manual pages

Every console entrypoint has an authored reStructuredText source in
`docs/man/`, and the wheel build generates that command's section-one manual
from it. The `hatch-build-scripts` hook and Docutils are build-system
requirements, not runtime dependencies: they are declared in
`[build-system] requires` and must never appear in `[project] dependencies`. The
[manpage packaging design](manpages-design.md) records the decision, and the
[users' guide](users-guide.md) documents installation and how to read the
installed pages.

The hook's single entry in
`[[tool.hatch.build.targets.wheel.hooks.build-scripts.scripts]]` runs Docutils'
`rst2man` once per console script, reading `<command>.rst` and writing
`<command>.1`. Generation is bounded to `docs/man/` by that entry's `work_dir`
and `out_dir`, with cleanup and artefact registration disabled, so the plugin
never scans the checkout root and cannot race with uv pruning its build cache.
`tests/unit/test_manpage_sources.py` pins those settings, the `rst2man` command
list, and the one-source-per-console-script inventory, so a revert to the
plugin's defaults fails the suite.

`docs/man/docutils.conf` makes warnings fatal, selects UTF-8 input and output,
and disables datestamps, generator metadata, raw content, and file insertion. A
malformed source therefore fails the build rather than shipping an incomplete
page or one that depends on the build host. A missing source fails it too: the
generator never falls back to a page already on disk.

The wheel's `shared-data` mapping puts each generated page under
`share/man/man1/` of the distribution's data scheme, so an installer unpacks it
as `<environment>/share/man/man1/<command>.1` without running a generator. The
source distribution instead keeps the `.rst` sources, the Docutils
configuration, and the build configuration, and excludes the generated `.1`
pages, so a subsequent wheel build regenerates them rather than reusing build
outputs.

Generated pages are build output only. They are ignored by Git and regenerated
in place whenever the wheel build hook runs, so a stale `docs/man/*.1` never
reaches a distribution. Edit the `.rst` source and rebuild; never edit or
commit a `.1` file.

A change to a console script's arguments or options must update
`docs/man/<command>.rst` and the users' guide in the same change.
`tests/unit/test_manpage_sources.py` resolves the command-line parameters from
`git_donkey/cli.py` and fails when a manual omits one. It follows Cyclopts
`Parameter(name="*")` spreads: the annotated type's fields, not the parameter's
own name, are the options that the manual must document.
`tests/integration/test_manpage_packaging.py` builds both distributions and
checks the packaged and installed pages. It seeds stale pages and malformed or
missing sources, so it also proves that a build replaces or refuses them rather
than shipping what it found.

Build a wheel and preview the generated page:

```shell
uv build --wheel
man -l docs/man/git-donkey.1
```

Build both distributions to check the source boundary; the `build-release`
target invokes the same build backend:

```shell
uv build
```

Run the focused manual tests:

```shell
uv run pytest tests/unit/test_manpage_sources.py \
  tests/integration/test_manpage_packaging.py
```

The packaging tests drive `uv` offline, so `make build` must run first to
populate the cache they reuse. `make test` depends on `make build` and runs
both manual test files as well.
