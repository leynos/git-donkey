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
command from the outside, and the [default-base and pull-mode
design](default-base-and-pull-modes.md) records the behavioural contract. This
section describes the pipeline and the invariants a contributor must preserve
when editing the workflow.

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
the worktree is created. Failures route through `helpers._die()`, which writes
a `git-donkey:`-prefixed message to stderr and raises `SystemExit`:
conflicting pull flags and precondition failures exit 2, while fetch, pull,
worktree, and filesystem failures exit 1. `_apply_template_overlay()` is the
one step that reports failure by returning `False`; `run_git_donkey()` then
returns 1 after the helper prints the reason. A missing overlay, or a
repository whose template directory cannot be selected, is not a failure.

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
`no_pull` participates in the same mutual-exclusion check, so it stays valid
on its own and conflicts with either pull flag.

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
  holds and that this command cannot pull into. A base with no local branch
  has nothing to update and counts as zero behind.
- In the workflow, the prompt is the only path to
  `_update_base_branch_in_worktree()`. `_maybe_update_base_branch()` prompts
  through `helpers._prompt_yes_no()` and skips the update when the answer is
  no or the terminal is non-interactive.

### Base resolution

Explicit bases are resolved by `choose_base_branch()`, which returns the
argument unchanged except for `.`, which selects the branch checked out in the
calling directory. That branch was captured during context loading by
`helpers._get_checked_out_branch_name()`, so a detached HEAD fails before
resolution.

Implicit discovery is deliberately a command rather than a query.
`_fetch_remote_default_ref()` reads the principal remote's advertised symbolic
`HEAD` with `ls_remote --symref` and fetches the named branch into
`refs/remotes/{remote}/{branch}`. `_advertised_default_branch()` is the pure
parser for the advertisement and is tested on its own. Fetching explicitly
matters because a narrow fetch configuration may omit the default branch, and
a stale local `remote/HEAD` alias is not trusted.

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
`outcome`, and at most one label from `pull_mode`, `base_kind`, or
`error_kind`.

- `pull_mode_selection`: `not_requested`, `selected`, `rejected`.
- `remote_default_discovery`: `success`, or `failure` with
  `git_command_error` or `missing_advertised_default`.
- `default_branch_fetch`: `success`, or `failure` with `git_command_error`.
- `base_update`: `not_requested`, `not_behind`, `declined`, `started`,
  `success`, or `failure` with `git_command_error` or `base_not_in_worktree`.
- `worktree_creation`: `started`, `success`, or `failure`.
- `template_overlay`: `unavailable` (with `selection_error` when the template
  directory cannot be selected), `started`, `success`, or `failure` with
  `os_error`.

Remote default discovery, the default-branch fetch, pull execution, and
worktree creation are also timed; a span reports its operation name and
duration only.

Every attribute comes from a fixed vocabulary, so records stay aggregatable.
Branch names, filesystem paths, remote URLs, Git output, exception text, and
template directory names are never recorded, because those values have
unbounded cardinality or disclose local information.

Records are not exported. `NullRecorder` is the default and discards them; the
module starts no process and opens no connection. Installing `LoggingRecorder`
routes records through the structured `extra` convention described under
[Operational logging](#operational-logging) instead, and changes nothing else
about how the command behaves.

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

`git-plonk` is split between pure completion policy, CLI parsing, and
infrastructure mutation:

- `git_donkey.cli` exposes the `git-plonk` console script and maps `--soft`,
  `--hard`, and `--dry-run` to `git_donkey.plonk.run_git_plonk()`.
- `git_donkey.plonk_policy` owns branch-name and commit-message policy. It maps
  issue branches such as `issue-123-title` to `(#123)`, maps roadmap branches
  such as `road-1-2-3a-4-title` to `(road.1.2.3a.4)`, and selects candidates
  whose markers appear in history. It must stay free of GitPython, filesystem,
  and process mutation.
- `git_donkey.plonk` owns repository discovery, git-donkey worktree discovery,
  generated-directory cleanup, Git worktree removal, local branch deletion,
  dry-run planning, and user-facing summaries.

`git_donkey.plonk_policy.completed_candidates` is generic over its candidate
type: it accepts any iterable whose items expose a read-only `marker` property
returning `str`, returns the matching candidates unchanged, and never mutates
the candidates it receives. `git_donkey.plonk` therefore passes its worktree
candidates directly.

The plonk workflow deliberately reads completion history from the canonical
trunk ref. This allows `git plonk` to be invoked from a linked topic worktree
while still using the trunk history that contains issue or roadmap merge
markers.

Default and hard modes only consider linked worktrees under
`../{repo}.worktrees` and only remove worktrees whose branch-derived completion
marker is present in canonical trunk history. Hard mode deletes local branches
after that marker check succeeds; it does not delete remote branches. Soft mode
uses the same git-donkey worktree discovery but only removes conventional
generated directories such as `target`, `node_modules`, `.venv`, and cache
directories.

`pytest-bdd` and `syrupy` are development dependencies for this command.
`pytest-bdd` covers user workflows against real temporary Git repositories, and
`syrupy` pins stable summary rendering. Hypothesis checks marker-shape
invariants in the pure policy layer.

## Operational logging

`git-fafo` and `git-plonk` log decision boundaries without logging secrets.
Stable fields are provided through `extra`, so callers can route records into
structured logging later:

- `token_source` records whether credentials came from the environment, cache,
  or device flow.
- `operation` records GitHub and Git operations such as repository creation,
  local initialization, push, generated-path cleanup, worktree removal, and
  branch deletion.
- `repo_name`, `owner`, `branch`, `result`, and adoption `reason` provide
  diagnostic context for repository decisions.
- `mode`, `worktree`, `marker`, `candidate_count`, `completed_count`, and
  `removed_count` provide diagnostic context for plonk cleanup decisions.

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

`tests/integration/conftest.py` also provides the `stub_commands` fixture. It
creates temporary `git` and `copier` executables that append their command-line
arguments to a log file. Scaffold workflow tests should use this fixture
instead of writing per-test command stubs.

`tests/integration/_fafo_adoption_stubs.py` builds local bare remotes with
specific histories for adoption tests. Use these helpers when adding new
existing-repository scenarios, so the tests stay focused on behaviour rather
than Git setup.

`tests/unit/test_fafo_error_messages.py` pins complete user-facing error
messages. Add new cases there when a new `git-fafo` conflict or credential
failure path is introduced.
