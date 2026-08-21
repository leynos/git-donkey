# Developer guide

This guide records internal module boundaries and local tooling conventions for
contributors working on `git-donkey`.

## Spelling policy

Run `make spelling` to enforce en-GB-oxendict prose spelling. The generated
`typos.toml` starts from the shared estate dictionary, refreshes its untracked
local cache only when the authority is newer, and then applies the narrow
repository policy in `typos.local.toml`. Edit the local policy and regenerate
the configuration rather than changing generated entries by hand.

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

`make lint` runs Skylos `4.33.2` after the existing Ruff, docstring, and
static-analysis checks. The production-only scan covers `git_donkey`, reports
only dead-code findings, does not upload results or collect provenance, and
fails the local gate and continuous integration when it finds an unexplained
symbol.

Treat every finding as dead code until its caller is verified. Remove genuine
dead code. When a dynamic runtime boundary makes a finding a false positive,
run:

```shell
make skylos-allow NAME=symbol REASON="Verified runtime caller"
```

The helper refuses empty values and records the symbol and explanation in
`[tool.skylos.whitelist]` in `pyproject.toml`. Do not add speculative, bulk, or
unexplained allow-list entries.

## Tool pinning

The `Makefile` pins Ruff with `RUFF_VERSION` and ty with `TY_VERSION`, and
invokes each through the `RUFF` and `TY` variables, which run
`uv tool run ruff@$(RUFF_VERSION)` and `uv tool run ty@$(TY_VERSION)`. Every
invocation therefore uses the pinned version regardless of which `ruff` or `ty`,
if any, is on `PATH`, so local runs cannot silently diverge from continuous
integration.

Pin both deliberately. Rule sets differ between Ruff releases and diagnostics
differ between ty releases, so an unpinned tool reports problems in one
environment that never appear in the other.

`TY_VERSION` is the sole ty version declaration: continuous integration runs
`make typecheck` and installs no separate ty. Ruff is also installed as a
development dependency and as a continuous integration tool, so keep
`RUFF_VERSION`, the `ruff==` entry in `pyproject.toml`, and the
`uv tool install ruff==` step in `.github/workflows/ci.yml` in step.

The `typecheck` target adds `scripts` to the type checker's module search path,
because that directory holds PEP 723 single-file helpers that import each other
by module name. Keep the path scoped to the target rather than changing
application import paths.

## Lint workflow

`make lint` runs six checks in order. The `lint` target invokes them as:

```make
$(RUFF) check
$(UV_ENV) uv run interrogate --fail-under 100 git_donkey
pyscn check git_donkey tests --skip-clones
$(PYLINT_BUILTIN) $(PYLINT_TARGETS)
$(PYLINT_DF12) $(PYLINT_TARGETS)
$(UV_ENV) uv run ambrleaks tests
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

The two Pylint passes share `PYLINT_TARGETS`, which defaults to
`git_donkey scripts tests`, so both always analyse the same files. It is
declared with `?=` and can be overridden on the command line. `PYLINT_JOBS` is
a tenth of the machine's cores with a floor of two, keeping the pass parallel
on small continuous integration runners while leaving headroom on large shared
machines.

Running `make lint` requires `uv` and `pyscn` on `PATH`; the Makefile's
`TOOLS` list and `ensure_tool` check fail early with a clear message if
`uv` is missing. `pyscn` is the one lint tool `uv` does not provide, so
continuous integration installs it with `uv tool install pyscn`.
Everything else needs no separate installation: Ruff runs via
`uv tool run` at a pinned version, while interrogate, both Pylint
passes, and ambrleaks run via `uv run` from the project virtual
environment. The project requires Python 3.13 or newer, but `uv`
provisions a suitable CPython interpreter for the virtual environment,
so contributors need no matching system Python. No Node.js tooling is
needed for `make lint`; that belongs to the separate `markdownlint` and
`nixie` targets.

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
