# Developer guide

This guide records internal module boundaries and local tooling conventions for
contributors working on `git-donkey`.

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

## Operational logging

`git-fafo` logs decision boundaries without logging secrets. Stable fields are
provided through `extra`, so callers can route records into structured logging
later:

- `token_source` records whether credentials came from the environment, cache,
  or device flow.
- `operation` records GitHub and Git operations such as repository creation,
  local initialization, and push.
- `repo_name`, `owner`, `branch`, `result`, and adoption `reason` provide
  diagnostic context for repository decisions.

## Ruff pinning

The `Makefile` pins Ruff with `RUFF_VERSION`. The `ruff` target first verifies
that the `ruff` executable exists, then calls
`scripts/check-ruff-version.sh "$(RUFF_VERSION)"`.

Keep `RUFF_VERSION`, the helper script, and continuous integration tool
versions in sync. The helper exists, so the Make target stays short enough for
Makefile linting while keeping the version check easy to read and reuse.

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
