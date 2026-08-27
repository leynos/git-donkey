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

## Ruff pinning

The `Makefile` pins Ruff with `RUFF_VERSION`. The `ruff` target first verifies
that the `ruff` executable exists, then calls
`scripts/check-ruff-version.sh "$(RUFF_VERSION)"`.

Keep `RUFF_VERSION`, the helper script, and continuous integration tool
versions in sync. The helper exists, so the Make target stays short enough for
Makefile linting while keeping the version check easy to read and reuse.

## Ty pinning

The `typecheck` target provisions Ty at `TY_VERSION`, so local runs and
continuous integration use the same analyser release. It includes `scripts` as
an additional module search path because the standalone spelling-policy script
imports adjacent repository modules. Keep this path scoped to the target rather
than modifying application import paths.

## Dead-code detection

`make lint` has four Python lint tiers, which run in this order:

1. Ruff performs fast source and docstring-style checks.
2. `interrogate` requires complete docstring coverage.
3. `pyscn` applies static analysis to application and test code.
4. Skylos performs strict production dead-code detection.

Skylos `4.33.2` scans only `git_donkey`, explicitly excludes `tests`, reports
only dead-code findings, does not upload results or collect provenance, and
blocks local linting and continuous integration on unexplained code. Skylos
parses source with its own runtime Abstract Syntax Tree (AST), so its
command-only CLI macro pins Python 3.14. The pin prevents newer Python syntax
from producing phantom dead-code findings. The separate `$(SKYLOS)` macro adds
scan-only options such as `--config-file` for the lint target.

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
