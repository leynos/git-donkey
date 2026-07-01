# Implement Git incoming and outgoing commands

This ExecPlan (execution plan) is a living document. The sections `Constraints`,
`Tolerances`, `Risks`, `Progress`, `Surprises & discoveries`, `Decision log`,
and `Outcomes & retrospective` must be kept up to date as work proceeds.

Status: DRAFT

## Purpose / big picture

After this change, users can run `git incoming`, `git in`, `git outgoing`, or
`git out` from inside a Git repository and see the branch commits that would be
pulled from, or pushed to, the configured upstream branch. The commands should
feel familiar to Mercurial users: `incoming` reports changes present in the
source and absent locally, `outgoing` reports changes present locally and
absent from the destination, and the commands return `0` when such changes
exist and `1` when none exist.

The Mercurial references for this behaviour are the upstream command help pages
for [`hg incoming`](https://mercurial-scm.org/help/commands/incoming) and
[`hg outgoing`](https://mercurial-scm.org/help/commands/outgoing). This plan
intentionally maps Mercurial bookmarks to Git branches. It does not implement
Mercurial bundles, templates, phases, or bookmark comparison output.

## Constraints

- Preserve the existing `git donkey`, `git track`, `git fafo`, and
  `git donkey-template` behaviours and public entrypoints.
- Use Git branches and upstream tracking refs in place of Mercurial bookmarks.
- Use the repository's existing Python 3.13, Cyclopts, GitPython, Ruff, ty, and
  pytest tooling.
- Do not add a new external runtime dependency unless a tolerance exception is
  approved.
- Implement tests before production code for every new behaviour.
- Update `docs/users-guide.md` for the new user-facing commands before the
  implementation is considered complete.
- Gate code changes with `make check-fmt`, `make lint`, `make typecheck`, and
  `make test`, writing command output to branch-specific files in `/tmp`.
- Gate Markdown-only plan changes with `make markdownlint` and `make nixie`,
  writing command output to branch-specific files in `/tmp`.
- Keep generated caches such as `.memdb/`, `.uv-cache/`, `.uv-tools/`,
  `.pytest_cache/`, and `.venv/` out of Git unless the project already tracks
  them.

## Tolerances

- Scope: stop and escalate if the implementation needs more than eight
  non-test source files or more than 450 net lines of production code.
- Interface: stop and escalate if any existing public function signature or
  console script must change incompatibly.
- Dependencies: stop and escalate before adding any package dependency.
- Semantics: stop and escalate if matching Mercurial's documented exit-code
  semantics conflicts with established Git or project behaviour.
- Remote selection: stop and present options if the first remote, `origin`, and
  branch upstream configuration imply different defaults that materially affect
  user-visible behaviour.
- Iterations: stop and escalate if the same focused test still fails after
  three implementation attempts.
- Ambiguity: stop and present options if support for a Mercurial flag cannot be
  mapped cleanly to Git branch semantics.

## Risks

- Risk: Mercurial's changeset model differs from Git's branch and upstream ref
  model. Severity: medium. Likelihood: high. Mitigation: define the supported
  Git semantics explicitly around `HEAD...@{upstream}` comparisons and defer
  unsupported Mercurial flags.
- Risk: Exit code `1` for "no changes" can look like command failure to shell
  scripts. Severity: medium. Likelihood: medium. Mitigation: document the
  return codes in the user guide and cover them in tests.
- Risk: Repositories without an upstream branch have no obvious default source
  or destination. Severity: medium. Likelihood: medium. Mitigation: return `2`
  with a clear error message unless the user supplies an explicit branch or ref.
- Risk: Fetching before comparison can fail or mutate remote-tracking refs.
  Severity: low. Likelihood: medium. Mitigation: make fetch the default because
  Mercurial describes the commands in terms of the current pull or push result,
  provide `--no-fetch` as an explicit escape hatch, and test both default and
  no-fetch paths.

## Progress

- [x] (2026-07-01 00:00Z) Drafted the pre-implementation ExecPlan.
- [ ] Review and approve this ExecPlan before implementation begins.
- [ ] Add red tests for incoming and outgoing branch comparisons.
- [ ] Implement the minimal command module and console entrypoints.
- [ ] Update user-facing documentation.
- [ ] Run full quality gates and commit the implementation.
- [ ] Review changed production code for small follow-up refactors.

## Surprises & discoveries

- Observation: The project already exposes Git subcommands through
  `pyproject.toml` console scripts whose names begin with `git-`. Evidence:
  `git-donkey`, `git-track`, `git-fafo`, and `git-donkey-template` are
  registered under `[project.scripts]`. Impact: The new commands should follow
  that pattern by adding `git-incoming`, `git-in`, `git-outgoing`, and
  `git-out` scripts rather than inventing a dispatcher.
- Observation: Integration tests already create temporary local and bare Git
  repositories. Evidence: `tests/integration/conftest.py` provides
  `_setup_repo()` and `_seed_repo()`. Impact: New behavioural tests can reuse
  those helpers instead of creating a separate fixture layer.

## Decision log

- Decision: Treat a branch's configured upstream as the default source for
  `git incoming` and the default destination for `git outgoing`. Rationale:
  Mercurial defaults to the default pull or push location. In Git, the branch
  upstream is the closest branch-level equivalent and avoids guessing from
  unrelated remotes. Date/Author: 2026-07-01, Codex.
- Decision: Add short aliases as separate console scripts, `git-in` and
  `git-out`, which call the same runners as `git-incoming` and `git-outgoing`.
  Rationale: Git discovers subcommands by executable name, so separate scripts
  are the simplest way to make `git in` and `git out` work consistently.
  Date/Author: 2026-07-01, Codex.
- Decision: Use `git log --oneline --decorate --no-merges` style output as the
  first implementation target, with optional merge inclusion controlled by a
  flag. Rationale: Mercurial examples show brief and patch-oriented output, but
  the core value is identifying candidate commits. The concise log is
  observable, familiar to Git users, and easy to validate. Date/Author:
  2026-07-01, Codex.

## Outcomes & retrospective

This plan has not yet been implemented. The intended outcome is a tested,
documented command pair that gives a Mercurial-style preview of branch movement
before pull or push operations.

## Context and orientation

The project is a Python package named `git-donkey`. Console entrypoints are
declared in `pyproject.toml` under `[project.scripts]`. The existing
entrypoints are implemented in `git_donkey/cli.py` using Cyclopts `App`
instances, and each CLI wrapper delegates to a workflow module such as
`git_donkey/track.py`.

Shared Git helpers live in `git_donkey/helpers.py`. The helper
`_find_repo(prefix)` locates the current repository,
`_fetch_remote(repo, remote, prefix)` fetches remote refs, and
`_first_remote_name(repo, prefix)` selects a remote when needed. The existing
`git_donkey/donkey.py` function `_ahead_behind(repo, base, compare_ref)` shows
the current project style for using `git rev-list --left-right --count`.

Integration tests live in `tests/integration/`. The helpers
`tests/integration/conftest.py::_setup_repo()` and `_seed_repo()` create local
and bare repositories with a seeded `main` branch. Unit tests live in
`tests/unit/`. Documentation for users lives in `docs/users-guide.md`.

In this plan, "upstream branch" means the Git branch configured for the current
branch with `git branch --set-upstream-to`, visible through
`git rev-parse --abbrev-ref --symbolic-full-name @{upstream}`. "Incoming" means
commits reachable from the upstream branch and not reachable from `HEAD`.
"Outgoing" means commits reachable from `HEAD` and not reachable from the
upstream branch.

## Plan of work

Stage A confirms command shape without production changes. Re-read
`git_donkey/cli.py`, `git_donkey/helpers.py`, `git_donkey/track.py`,
`pyproject.toml`, `docs/users-guide.md`, and the existing integration tests.
Confirm whether the current branch has an upstream in the temporary test
repositories, and decide whether a small test helper is needed to configure
one. This stage ends when the target file list is stable.

Stage B adds red tests. Create `tests/unit/test_incoming_outgoing.py` for pure
comparison helpers and `tests/integration/test_git_incoming_outgoing.py` for
end-to-end repository behaviour. The first focused tests should specify that
`run_git_incoming()` returns `0` and prints the remote-only commit when the
upstream is ahead, returns `1` and prints no commit when there are no
remote-only commits, and returns `2` with a clear error when no upstream is
configured. Equivalent tests should specify that `run_git_outgoing()` returns
`0` for local-only commits and `1` when there are none. Run the focused tests
before adding production code and record the expected import or attribute
failure.

Stage C implements the minimal functionality. Add a new module
`git_donkey/incoming_outgoing.py` containing:

```python
def run_git_incoming(ref: str | None = None, *, fetch: bool = True) -> int: ...


def run_git_outgoing(ref: str | None = None, *, fetch: bool = True) -> int: ...
```

The implementation should locate the repository, resolve the comparison ref to
the explicit `ref` or the current branch upstream, fetch the owning remote when
`fetch` is true and the ref is remote-backed, compute commits with Git revision
ranges, and print a concise `git log --oneline --decorate` listing. Incoming
uses `<comparison-ref> --not HEAD`; outgoing uses
`HEAD --not <comparison-ref>`. No changes returns `1`. Found changes returns
`0`. Configuration or Git usage errors return `2` through the project's
existing error-reporting style.

Stage D wires the command line. Update `git_donkey/cli.py` to define Cyclopts
apps for `git incoming` and `git outgoing`, with matching entrypoint functions
for aliases. The commands should accept an optional comparison ref and a
`--no-fetch` flag. Update `pyproject.toml` with `git-incoming`, `git-in`,
`git-outgoing`, and `git-out` scripts.

Stage E updates documentation. Add the new commands to `docs/users-guide.md`,
including examples, default upstream semantics, explicit ref usage,
`--no-fetch`, and the `0` or `1` Mercurial-style return codes. If the README
command overview is still maintained as a concise list, add a short mention
there too. Record any design decision that affects future users in a new or
existing design document only if the final implementation diverges from this
plan.

Stage F refactors only if the finished code shows real duplication or complex
conditionals. Any refactor must be a separate commit after the functional
change and must preserve the tests added in Stage B.

## Concrete steps

Run commands from the repository root:

```shell
cd /home/leynos/.lody/repos/github---leynos---git-donkey/worktrees/de30dcf0-099d-48c0-a955-a807f568fe37
```

Create red tests, then run the focused test file:

```shell
ACTION=test-incoming-outgoing
LOG="/tmp/${ACTION}-git-donkey-$(git branch --show-current).out"
make build 2>&1 | tee "/tmp/build-git-donkey-$(git branch --show-current).out"
UV_CACHE_DIR=.uv-cache UV_TOOL_DIR=.uv-tools uv run pytest -v \
  tests/unit/test_incoming_outgoing.py \
  tests/integration/test_git_incoming_outgoing.py 2>&1 | tee "$LOG"
```

Expected red-stage transcript before implementation:

```plaintext
E   ImportError: cannot import name 'incoming_outgoing' from 'git_donkey'
```

After adding the implementation, rerun the same focused command. Expected
green-stage result:

```plaintext
tests/unit/test_incoming_outgoing.py::... PASSED
tests/integration/test_git_incoming_outgoing.py::... PASSED
```

Run the full code gates sequentially:

```shell
make check-fmt 2>&1 | tee "/tmp/check-fmt-git-donkey-$(git branch --show-current).out"
make lint 2>&1 | tee "/tmp/lint-git-donkey-$(git branch --show-current).out"
make typecheck 2>&1 | tee "/tmp/typecheck-git-donkey-$(git branch --show-current).out"
make test 2>&1 | tee "/tmp/test-git-donkey-$(git branch --show-current).out"
```

For documentation-only revisions to this plan, run:

```shell
make markdownlint 2>&1 | tee "/tmp/markdownlint-git-donkey-$(git branch --show-current).out"
make nixie 2>&1 | tee "/tmp/nixie-git-donkey-$(git branch --show-current).out"
```

## Validation and acceptance

The feature is accepted when the following behaviour is observable in a
temporary repository with `feature/demo` tracking `origin/feature/demo`:

- After another clone pushes one commit to `origin/feature/demo`, running
  `git incoming` or `git in` from the local branch fetches, prints that commit,
  and exits `0`.
- Running `git outgoing` or `git out` in the same state prints no commits and
  exits `1`.
- After the local branch adds one unpushed commit, running `git outgoing` or
  `git out` prints that commit and exits `0`.
- Running `git incoming` in that state prints no commits and exits `1`.
- Running either command on a branch with no upstream and no explicit ref exits
  `2` with a message explaining how to set an upstream or pass a ref.
- Running either command with `--no-fetch` compares against the current local
  remote-tracking ref without contacting the remote.

The final implementation must pass:

- `make check-fmt`
- `make lint`
- `make typecheck`
- `make test`
- `make markdownlint`, if Markdown files changed
- `make nixie`, if Markdown files changed

## Idempotence and recovery

The test repository setup is disposable and uses pytest `tmp_path`, so focused
tests can be rerun safely. Fetching in the implementation updates
remote-tracking refs, matching the intended preview semantics. If a local
manual test repository is used, create it under a scratch directory and remove
it after validation.

If the implementation produces incorrect command output, first inspect the
captured `/tmp/*-git-donkey-incoming-outgoing-commands.out` logs, then rerun
only the focused tests. Do not proceed to full gates until the focused tests
pass.

## Artifacts and notes

The Mercurial help pages establish these behaviours to preserve:

```plaintext
incoming: show changesets found in the source that would be pulled.
outgoing: show changesets not found in the destination that would be pushed.
return 0 when such changes exist, and 1 otherwise.
```

The project patterns to follow are:

```plaintext
pyproject.toml            declares git-* console scripts.
git_donkey/cli.py         maps console scripts to workflow runners.
git_donkey/helpers.py     centralizes repository discovery and Git failures.
tests/integration/        exercises Git workflows against temporary repos.
docs/users-guide.md       documents user-facing command behaviour.
```

## Interfaces and dependencies

Add `git_donkey/incoming_outgoing.py` with these public runner functions:

```python
def run_git_incoming(ref: str | None = None, *, fetch: bool = True) -> int:
    """Report commits that would be pulled from the comparison ref."""


def run_git_outgoing(ref: str | None = None, *, fetch: bool = True) -> int:
    """Report commits that would be pushed to the comparison ref."""
```

Add these console entrypoint functions in `git_donkey/cli.py`:

```python
def git_incoming() -> None:
    """Console entrypoint for git-incoming."""


def git_in() -> None:
    """Console entrypoint for git-in."""


def git_outgoing() -> None:
    """Console entrypoint for git-outgoing."""


def git_out() -> None:
    """Console entrypoint for git-out."""
```

Register these scripts in `pyproject.toml`:

```toml
git-incoming = "git_donkey.cli:git_incoming"
git-in = "git_donkey.cli:git_in"
git-outgoing = "git_donkey.cli:git_outgoing"
git-out = "git_donkey.cli:git_out"
```

No new external dependencies are planned. Use GitPython and Git's existing
revision selection commands through `repo.git`.

## Revision note

Initial draft created on 2026-07-01. It defines the supported Git mapping for
Mercurial-style incoming and outgoing commands, the test-first implementation
sequence, and the validation gates required before code changes can be accepted.
