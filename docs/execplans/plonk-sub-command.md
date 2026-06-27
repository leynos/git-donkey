# Add `git plonk`

This ExecPlan (execution plan) is a living document. The sections `Constraints`,
`Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`, `Decision Log`,
and `Outcomes & Retrospective` must be kept up to date as work proceeds.

Status: COMPLETE

## Purpose / big picture

This change adds a new Git subcommand, `git plonk`, exposed by the `git-plonk`
console script. A user can run it inside any worktree of a repository managed by
`git donkey` and reclaim disk space from old linked worktrees. In default mode
it removes completed worktrees whose branch names map to merge markers already
present in the current history. In `--soft` mode it removes generated build and
dependency directories from linked worktrees without removing the worktrees. In
`--hard` mode it removes completed worktrees and then deletes the matching
local branches.

The observable result is that focused `pytest` and `pytest-bdd` tests pass,
`git plonk --help` documents the three modes, and integration tests using real
temporary Git repositories show the expected worktrees, branches, and heavy
directories being removed or retained.

## Constraints

- Keep the new command consistent with the existing `git_donkey.cli` console
  script pattern.
- Do not remove arbitrary directories. Only operate on linked worktrees under
  the `../{repo}.worktrees` directory derived from the main worktree path.
- Default and `--hard` mode must only remove worktrees whose branch names are
  recognized and whose corresponding marker appears in `HEAD` history.
- `--soft` mode must not remove worktrees or branches.
- `--hard` mode must not delete the currently checked-out branch.
- Follow Red-Green-Refactor: tests first, observe focused failure, then
  implement the smallest passing change.
- Use Makefile targets for quality gates and capture command output with
  `tee` logs under `/tmp`.
- Do not run tests, linters, formatters, or typecheckers in parallel.
- Do not create an isolated Cargo cache.
- Keep documentation changes compliant with `docs/documentation-style-guide.md`.

## Tolerances (exception triggers)

- Stop and ask if deleting a branch requires deleting a remote branch; this
  plan only covers local branch deletion.
- Stop and ask if worktree detection requires operating outside the
  `../{repo}.worktrees` root.
- Stop and ask if `pytest-bdd`, `syrupy`, or `hypothesis` cannot be installed
  through project development dependencies.
- Stop and ask if any full quality gate fails for a reason unrelated to this
  change after one focused investigation and fix attempt.
- Stop and ask before deleting any remote branch. Local hard-mode branch
  deletion is allowed after the marker check succeeds.

## Risks

- The roadmap marker syntax in the request is compact and has no examples in
  the repository. The plan interprets a branch prefix matching
  `(?:(\w+)-)?(\d+)-(\d+)-(\d+)(\w+)?-(?:(\d+)-)?` as a dotted marker: optional
  namespace, then `major.minor.patch`, optional letter suffix attached to the
  patch number, and optional task number. For example, `road-1-2-3a-4-title`
  maps to `(road.1.2.3a.4)`, and `1-2-3-title` maps to `(1.2.3)`. The matcher
  also accepts one optional dot before the closing parenthesis because the
  request shows `\.?`.
- Worktree removal is destructive. The implementation mitigates this by
  limiting candidates to Git's porcelain worktree list and to paths under the
  expected `git donkey` worktree root.
- Soft cleanup can delete useful generated state. The directory list will be
  explicit, documented, and limited to conventional build, dependency, cache,
  coverage, and virtual-environment directories.
- Squash-merge workflows can produce the requested marker in history without
  making the local branch an ancestor of `HEAD`. Hard mode therefore treats the
  marker match as the safety check for local branch deletion.

## Progress

- [x] 2026-06-27: Loaded the `leta`, `python-router`, `python-testing`,
  `python-verification`, and `execplans` skills.
- [x] 2026-06-27: Created a leta workspace for this repository.
- [x] 2026-06-27: Renamed the local branch to `plonk-sub-command`.
- [x] 2026-06-27: Inspected existing CLI entrypoints, worktree helpers,
  Makefile gates, and user documentation.
- [x] 2026-06-27: Added and built test dependencies for `pytest-bdd` and
  `syrupy`.
- [x] 2026-06-27: Added unit, property, snapshot, and BDD tests for
  `git plonk`.
- [x] 2026-06-27: Ran focused tests and observed the expected red import
  failure for missing `git_donkey.plonk`.
- [x] 2026-06-27: Implemented `git_donkey.plonk`, wired the CLI, added the
  console script, generated the new syrupy snapshot, and passed 10 focused
  tests.
- [x] 2026-06-27: Updated the README and users' guide for `git plonk`.
- [x] 2026-06-27: Passed `make check-fmt`, `make lint`, `make typecheck`,
  `make test`, `make markdownlint`, and `make nixie`.
- [x] 2026-06-27: Confirmed `uv run git-plonk --help` shows the default,
  `--soft`, and `--hard` command surface.
- [x] 2026-06-27: Committed the implementation, pushed
  `plonk-sub-command` with upstream tracking to `origin/plonk-sub-command`, and
  opened draft PR <https://github.com/leynos/git-donkey/pull/17>.
- [x] 2026-06-27: Added review-requested regression coverage for mutually
  exclusive `--soft --hard` CLI use and for running `git plonk` from a linked
  topic worktree while the completion marker exists only on `main`.
- [x] 2026-06-27: Split completion-marker policy into pure
  `git_donkey.plonk_policy`, added Git/filesystem adapter boundaries in
  `git_donkey.plonk`, and added structured logging around cleanup start,
  candidate selection, removals, and failure boundaries.
- [x] 2026-06-27: Expanded module and developer documentation for
  `git-plonk`, including CLI entrypoint, module boundaries, test tooling, and
  logging fields.

## Surprises & Discoveries

- The repository already has Hypothesis available in the development
  dependency group, so generated invariant tests can be added without a new
  property-testing dependency.
- The repository did not previously depend on `pytest-bdd` or `syrupy`; they
  were added to the development dependency group and locked with `uv`.
- Existing `git donkey` worktrees are stored at `../{repo}.worktrees/{branch}`
  and can be safely identified by combining Git's porcelain worktree list with
  that derived root.
- Focused testing found the only first green-stage failure was the expected
  missing syrupy snapshot. Generating the snapshot made all 10 focused tests
  pass.
- Follow-up review found happy-path cleanup coverage was not enough to rule out
  broken flag handling or using the current topic worktree history instead of
  trunk history. The added regression tests now pin both behaviours.
- Follow-up review also found plonk policy was coupled to Git and filesystem
  mutation. The policy is now isolated in `git_donkey.plonk_policy`, while
  `git_donkey.plonk` owns infrastructure adapters and user summaries.

## Decision Log

- Decision: expose `git plonk` as a separate `git-plonk` console script and a
  `git_donkey.cli.git_plonk` entrypoint. Rationale: every existing Git
  subcommand in this package follows that pattern, and Git discovers
  subcommands from `git-*` executables on `PATH`.
- Decision: implement the domain logic in a new `git_donkey.plonk` module.
  Rationale: cleanup, marker matching, and branch deletion are separate from
  worktree creation and should be unit-testable without invoking Cyclopts.
- Decision: use Hypothesis rather than CrossHair for invariants.
  Rationale: branch-marker parsing is a pure string transformation with a
  compact generated input space. Hypothesis can generate recognized branches
  and prove the marker shape over many cases cheaply in normal test runs.
- Decision: use `syrupy` snapshots for command summaries, not destructive
  filesystem state. Rationale: snapshots are useful for stable user-visible
  report text while regular assertions are clearer for path and branch
  existence.
- Decision: use `pytest-bdd` for an integration scenario covering default,
  soft, and hard modes against real temporary Git repositories. Rationale:
  these behaviours are user workflows rather than isolated helper calls, and
  BDD keeps the expected mode differences explicit.
- Decision: use `git branch -D` for hard-mode local branch deletion after the
  marker check succeeds. Rationale: the requested completion signal is a merge
  marker in history. Requiring Git ancestry would fail common squash-merge
  branches that still have a valid completion marker. Remote branches remain
  out of scope.
- Decision: keep completion-marker policy in `git_donkey.plonk_policy` and use
  small Git/filesystem adapters in `git_donkey.plonk`. Rationale: marker
  derivation and history matching are pure domain rules that should remain
  testable without GitPython, `os.chdir`, `Path`, `shutil`, or branch mutation.
- Decision: add structured module logging rather than changing stdout.
  Rationale: users still need the concise summary, while operators and tests
  can observe cleanup decisions through stable `extra` fields such as `mode`,
  `operation`, `worktree`, `branch`, `marker`, `candidate_count`, and
  `completed_count`.

## Implementation Plan

First, add `pytest-bdd` and `syrupy` to the development dependency group in
`pyproject.toml`. Run
`make build 2>&1 | tee /tmp/build-git-donkey-plonk-sub-command.out` so the lock
and environment are updated through the repository's normal build path.

Next, add failing tests before production code:

- Unit tests in `tests/unit/test_plonk.py` for issue markers, roadmap markers,
  unrecognized branch names, merged candidate selection, and summary rendering.
- A Hypothesis property in `tests/unit/test_plonk.py` that generates valid
  roadmap prefixes and asserts that the derived marker is parenthesized, uses
  dot separators, contains no hyphen, and matches a commit message containing
  the exact marker with or without one trailing dot.
- Snapshot assertions in `tests/unit/test_plonk.py` for dry summary data or
  rendered operation summaries.
- A BDD feature in `tests/integration/features/git_plonk.feature` with
  scenarios equivalent to:

```gherkin
Feature: Clean git donkey worktrees
  Scenario: Default mode removes completed worktrees only
    Given a repository with completed and active git donkey worktrees
    When I run git plonk in default mode
    Then the completed worktree is removed
    And the active worktree remains
    And the completed branch remains

  Scenario: Soft mode removes generated directories without removing worktrees
    Given a repository with generated directories inside git donkey worktrees
    When I run git plonk in soft mode
    Then the generated directories are removed
    And the worktrees remain
    And the branches remain

  Scenario: Hard mode removes completed worktrees and branches
    Given a repository with a completed git donkey worktree
    When I run git plonk in hard mode
    Then the completed worktree is removed
    And the completed branch is deleted
```

Run focused tests before implementation with:

```shell
UV_CACHE_DIR=.uv-cache UV_TOOL_DIR=.uv-tools uv run pytest -q \
  tests/unit/test_plonk.py \
  tests/integration/test_git_plonk_bdd.py \
  2>&1 | tee /tmp/red-pytest-git-donkey-plonk-sub-command.out
```

The expected red result is an import error for `git_donkey.plonk` or missing
CLI symbols.

Then implement `git_donkey.plonk` with:

- `_PlonkMode`, using values `default`, `soft`, and `hard`.
- `_PlonkCandidate`, containing branch name, worktree path, and marker.
- `plonk_policy.completion_marker_for_branch`, deriving issue and roadmap
  markers from branch names.
- `plonk_policy.has_completion_marker`, matching exact or dotted markers in
  commit messages.
- `plonk_policy.completed_candidates`, filtering candidates by history.
- `_PlonkResult`, containing the mode and removed worktrees, branches, and
  generated paths for deterministic summary rendering.
- `_donkey_worktree_candidates(stanzas, worktrees_root) -> list[_PlonkCandidate]`.
- `_GitWorktreeAdapter`, containing GitPython-backed history, worktree removal,
  and local branch deletion.
- `_FilesystemCleanupAdapter`, containing generated-directory removal.
- `_remove_soft_targets(worktree_path: Path) -> list[Path]`.
- `_render_summary(result: _PlonkResult) -> str`.
- `run_git_plonk(*, soft: bool = False, hard: bool = False) -> int`.

Wire `git_donkey.cli` with a Cyclopts app named `git plonk`. Add mutually
exclusive `--soft` and `--hard` flags and expose `git_plonk()` as the console
entrypoint. Add `git-plonk = "git_donkey.cli:git_plonk"` to `pyproject.toml`.

Finally, document the new command in `docs/users-guide.md` and update
`README.md` command count and overview if needed. Run formatting and gates
sequentially with tee logs:

```shell
make fmt 2>&1 | tee /tmp/fmt-git-donkey-plonk-sub-command.out
make check-fmt 2>&1 | tee /tmp/check-fmt-git-donkey-plonk-sub-command.out
make lint 2>&1 | tee /tmp/lint-git-donkey-plonk-sub-command.out
make typecheck 2>&1 | tee /tmp/typecheck-git-donkey-plonk-sub-command.out
make test 2>&1 | tee /tmp/test-git-donkey-plonk-sub-command.out
make markdownlint 2>&1 | tee /tmp/markdownlint-git-donkey-plonk-sub-command.out
make nixie 2>&1 | tee /tmp/nixie-git-donkey-plonk-sub-command.out
```

## Validation

Focused validation passes when the new unit and BDD tests pass with `pytest`.
Full validation passes when these Makefile targets all succeed:
`make check-fmt`, `make lint`, `make typecheck`, `make test`,
`make markdownlint`, and `make nixie`.

The completed implementation passed `make check-fmt`, `make lint`,
`make typecheck`, `make test`, `make markdownlint`, `make nixie`, and
`uv run git-plonk --help` on 2026-06-27. After review-response tests and
refactors, the full test run reported `112 passed`.

After the gates pass, commit the implementation with a descriptive imperative
message, push with `git push -u origin plonk-sub-command`, and create a draft
pull request. Before creating the PR, run `echo ${LODY_SESSION_ID}` and include
`https://lody.ai/leynos/sessions/${LODY_SESSION_ID}` in a `## References`
section at the end of the PR body.

## Outcomes & Retrospective

Implemented `git-plonk` with default, `--soft`, and `--hard` modes. Default and
hard modes remove only recognized `git donkey` worktrees with matching issue or
roadmap completion markers in `HEAD` history; hard mode also deletes the local
branches. Soft mode removes conventional generated directories from all
`git donkey` worktrees without removing worktrees or branches.

Validation passed through focused tests, the full Makefile quality gates, and a
direct `git-plonk --help` smoke check. Draft PR
<https://github.com/leynos/git-donkey/pull/17> contains the required Lody
session reference.

Follow-up review fixes added missing CLI-conflict and trunk-history regression
coverage, isolated completion-marker policy in a pure module, added
Git/filesystem adapter boundaries, expanded developer documentation, and added
structured logging for cleanup decisions and failure boundaries.
