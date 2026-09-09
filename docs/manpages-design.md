# Manpage packaging

## Decision and scope

Generate a section-one manual for every console entrypoint during wheel builds.
Keep the existing Hatchling backend. Use Docutils to convert authored
reStructuredText sources rather than importing the command-line interface (CLI)
or introducing a custom roff renderer.

The sources in `docs/man/` describe command arguments, options, examples, and
relevant environment variables. Changes to CLI signatures or workflow behaviour
must update the corresponding manual and `docs/users-guide.md` together.

## Build and installation boundaries

`hatch-build-scripts` invokes `rst2man` for each source from the `docs/man/`
directory. Both the hook plugin and Docutils are build-system requirements,
not runtime dependencies. The hook runs for wheel builds, including editable
wheels. No generator runs while an installer unpacks a previously built wheel.

Both working and output directories are restricted to `docs/man/`, with hook
cleanup disabled. Even an empty artefact pattern list can trigger directory
traversal in the plugin; scanning the repository root can race with uv deleting
temporary build-cache entries.

`docutils.conf` makes warnings fatal, selects UTF-8 input and output, disables
wall-clock datestamps, and disables raw content and file insertion. This keeps
malformed manual sources from silently producing incomplete pages and prevents
manual directives from incorporating unrelated build-host files.

Hatchling's `shared-data` mapping places each generated page in
`<distribution>.data/data/share/man/man1/`.[^1] Explicit file mappings prevent
source documents or stale, unrelated pages from entering the installed manual
directory. The build hook does not also register these files as ordinary
artefacts, which would put duplicate copies in the wheel's importable payload.

Generated `.1` files are ignored by Git and explicitly excluded from source
distributions. Source distributions retain the `.rst` sources, Docutils
configuration, and build configuration. Building a wheel from an unpacked source
distribution regenerates every page without requiring a Git checkout.

The installer owns placement under the Python environment's data prefix. For
`uv tool install`, pages belong under
`<uv-tool-dir>/git-donkey/share/man/man1/`. The package does not create XDG
symlinks, change `MANPATH`, or use post-install hooks. The separate
`uv-tools-manpage-discover` tool will own user-manpath discovery, conflicts,
symlink creation, and stale-link cleanup; it is outside this change.

## Contributor workflow

Build a wheel and generate local preview files:

```shell
uv build --wheel
man -l docs/man/git-donkey.1
```

Build a source distribution and a wheel using the normal release workflow:

```shell
uv build
```

The existing `make build-release` target also invokes the configured backend.
Docutils generates roff directly; neither Pandoc nor a system roff formatter is
a build dependency.[^2] The preview command alone requires a manual-page viewer.

## Validation

Run `make build` first to install the development environment and populate uv's
build-dependency cache. Then run the focused tests or the normal test target:

```shell
uv run pytest tests/unit/test_manpage_sources.py tests/integration/test_manpage_packaging.py
make test
```

Source-contract tests compare manual names, build commands, and installed paths
with the console-script inventory. They also check manual sections, important
options, and CLI parameter coverage without importing workflow modules.

Packaging integration tests build both distributions, inspect wheel data paths
and `RECORD` hashes, and rebuild a wheel independently from the source archive.
They start with stale generated files to check that generation replaces them.
Missing or malformed sources must fail the build rather than reuse stale pages.

An installation test creates an isolated environment, installs the wheel with
`uv pip install --no-deps`, checks the installed page bytes, and confirms that
uninstallation removes them. It also checks that installation never creates the
user-manpath directory. Placement does not depend on the runtime dependency
graph, and that graph cannot be resolved offline: `loctocat` requires `halo`,
whose only published wheel targets Python 2. The subprocesses run offline
against the cache populated in `.uv-cache` by `make build`, not an outer
`UV_CACHE_DIR` supplied by an action, and Python downloads are disabled.

[^1]: [Hatchling wheel shared data](https://hatch.pypa.io/latest/plugins/builder/wheel/).
[^2]: [Docutils manpage writer](https://docutils.sourceforge.io/docs/user/manpage.html).
