# Repository layout

This document is the canonical description of the repository's shape and the
responsibilities of its major paths. `docs/developers-guide.md` links here
instead of duplicating the module maps.

## Top-level paths

```text
git_donkey/          production package and console-script entry points
tests/               unit, property, and behavioural suites plus fixtures
docs/                user, developer, operator, and design documentation
docs/man/            reStructuredText sources for the manpages
docs/execplans/      ExecPlans for multi-step repository work
skill/               agent-facing skill definitions and reference material
ansible/             host provisioning roles and playbooks
```

The tree is illustrative, not exhaustive: generated output such as `.venv/`,
`.uv-cache/`, and build artefacts is omitted. Production code lives in
`git_donkey/`; tests mirror its public behaviour in `tests/unit/` and exercise
real repositories in `tests/integration/`. Snapshots live beside the tests that
own them, and generated documentation artefacts under `docs/man/` are committed
only through their release process.

## git-fafo module boundaries

`git-fafo` is split across three modules, so infrastructure details stay out of
the orchestration path:

- `git_donkey.fafo` owns input validation, scaffold selection, local Git
  initialization, and push orchestration.
- `git_donkey.fafo_github` owns GitHub token discovery, OAuth device-flow
  fallback, repository creation, duplicate-repository detection, and adoption
  confirmation.
- `git_donkey.fafo_adoption` owns existing-remote classification. It accepts
  only remotes with no refs or one branch containing a single empty initial
  commit.

The command-line interface obtains the GitHub token before calling
`run_git_fafo()`. The workflow function therefore receives an explicit `token`
argument and can focus on validated workflow inputs rather than environment
variables, credential files, or OAuth prompts.

## git-plonk module boundaries

`git-plonk` is split across pure policy and selection, shared records, the
completion tiers, infrastructure adapters, cleanup execution, workflow
reporting, summary rendering, and CLI parsing:

- `git_donkey.cli` exposes the `git-plonk` console script and maps `--soft`,
  `--hard`, `--dry-run`, `--no-github`, and `--upstream-gone` to
  `git_donkey.plonk.run_git_plonk()` through `_PlonkRunOptions`.
- `git_donkey.plonk_policy` owns offline branch-name and commit-message policy.
  It recognizes issue branches (`(#123)`), df12-build roadmap branches
  (`(2.2.1)`), and legacy roadmap branches (`(road.1.2.3a.4)`), and scans
  history for markers, issue closing keywords, the exact branch named in a
  merge commit's subject, and bounded verbatim branch names. Merge evidence
  requires a commit with more than one parent. It also owns the pure
  pull-request acceptance rule, so the GitHub module only runs `gh` and parses
  its response. It returns completion evidence and stays free of GitPython,
  filesystem, and process mutation.
- `git_donkey.plonk_records` owns the values and vocabulary: modes, completion
  signals, upstream states, skip and unverified reasons, history messages,
  candidates, evidence, results, and contexts. It holds values only: no Git
  access and no filesystem access.
- `git_donkey.plonk_selection` turns parsed `git worktree list --porcelain`
  stanzas into every branched candidate strictly under the worktree root,
  carrying the marker, issue number, checked-out commit, and lock state the
  branch and registration imply. It is pure: it reads stanza values and path
  objects, never Git state or the filesystem.
- `git_donkey.plonk_completion` owns the fallback tiers. It fetches and caches
  one verdict per branch, applies the verdicts, and reports unresolved
  candidates as unverified. Fetch and reporting are separate steps. The GitHub
  lookup arrives already resolved in `_SweepState`, and this module times it as
  the `github_lookup` boundary.
- `git_donkey.plonk_github` owns raw GitHub remote URL parsing and the read-only
  `gh pr list` process call. It parses each row into a neutral pull-request
  record, pins the host, closes stdin, and maps every failure to unknown. The
  identity rule lives in `plonk_policy`, and the observation lives in the
  caller, so this module stays a parser and process boundary.
- `git_donkey.plonk_remote` owns the network-facing remote-advertisement probe
  behind the opt-in upstream check. It validates the configured ref, runs one
  bounded `git ls-remote` through an injected runner, times it, and reports
  present, gone, or unknown. It never mutates the repository.
- `git_donkey.plonk_adapters` owns the local Git and filesystem boundaries:
  history, the upstream configuration read, the cleanliness and identity
  preflight, unforced worktree removal, conditional branch deletion, and
  generated-directory cleanup. The preflight runs `git status` through an
  injected runner with a cleaned environment, so it never mutates process
  state. Remote queries are delegated to `plonk_remote`.
- `git_donkey.plonk_cleanup` performs the removals for completed candidates and
  tallies what was removed, skipped, or failed.
- `git_donkey.plonk_workflow` owns the invocation options, structured logs, and
  bounded observations shared by the workflow.
- `git_donkey.plonk_summary` renders the finished result. `_render_summary()` is
  what `run_git_plonk()` prints.
- `git_donkey.plonk` keeps orchestration: repository state, candidate
  preparation, tier composition, soft cleanup, and `run_git_plonk()`. It is the
  composition root: it parses the GitHub remote once, resolves `gh`
  availability and the default lookup, and emits the missing-tool notice.
- `git_donkey.remote_default` owns the principal-remote and default-branch
  discovery both commands use, so completion history and base selection cannot
  diverge.

`git_donkey.plonk_policy.completed_candidates` is generic over its candidate
type: it accepts any iterable whose items expose read-only `branch_name`,
`marker`, and `issue_number` properties, plus an iterable of history entries
(`str`, or `_HistoryMessage` carrying the commit's parent information), and
returns `(candidate, evidence)` pairs in input order. It never mutates the
candidates it receives. `git_donkey.plonk` therefore passes its worktree
candidates directly, and `_GitWorktreeAdapter.history_messages()` supplies the
merge status the merge-commit matcher requires.

The plonk workflow deliberately reads completion history from the canonical
trunk ref resolved by `remote_default`. This allows `git plonk` to be invoked
from a linked topic worktree while still using the trunk history that contains
issue or roadmap merge markers.

Default and hard modes consider every linked worktree under the worktrees root
`git_donkey.layout` resolves — the sibling `../{repo}.worktrees` by default, or
the directory `donkey.worktreesRoot` names — and resolve completion in three
tiers: offline history evidence first, the GitHub merged-pull-request fallback
second when the principal remote is GitHub and `gh` is installed, and the
opt-in gone-upstream check third. A candidate no tier resolves is reported under
`Unverified worktrees:` and kept. Before removing a completed worktree, the
sweep asks `_GitWorktreeAdapter.skip_reason()` whether Git would discard it
unprompted: worktrees with modified, staged, or untracked files, worktrees
whose directory is gone, and worktrees whose state cannot be read are skipped
and reported as `_SkippedWorktree` entries while the sweep continues. Removal
never passes `--force`. Hard mode deletes local branches after a successful
unforced removal, so a skipped worktree keeps its branch; it does not delete
remote branches. Soft mode uses the same git-donkey worktree discovery but only
removes conventional generated directories such as `target`, `node_modules`,
`.venv`, and cache directories.

A local branch Git refuses to delete is reported rather than fatal. The
worktree counts as removed, the branch is neither reported as removed nor
reported as skipped, and the failure gets its own `Failed branch deletions:`
summary section. The sweep continues with later candidates, and
`run_git_plonk()` returns exit status 1 for a run whose branch deletion failed;
a run that only skipped or left worktrees unverified still returns 0.

The decision table, the skip vocabulary, and the reasoning behind reporting
skips rather than forcing removal are recorded in the
[plonk cleanup policy](plonk-cleanup-policy.md).

`pytest-bdd` and `syrupy` are development dependencies for this command.
`pytest-bdd` covers user workflows against real temporary Git repositories, and
`syrupy` pins the exact summary rendering. Hypothesis checks marker-shape
invariants in the pure policy layer.

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
