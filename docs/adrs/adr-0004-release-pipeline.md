# Architectural decision record (ADR) 004: Attested, reconciled release pipeline

## Status

Accepted on 2026-11-27. `release.yml` builds, attests, and publishes the same
sdist and wheel bytes to PyPI and to the tag's GitHub Release, never
overwriting either destination.

## Date

2026-11-27.

## Context and problem statement

The 0.2.0 release exposed three defects in the previous pipeline:

- the wheel was first built at tag time, so a source distribution that could
  not be built only failed in the release run;
- the GitHub Release was created by `softprops/action-gh-release` and the
  wheels were attached by piping `find` into `xargs`; when the download
  produced no `dist/wheels-*` directory, `find` failed, `xargs` succeeded, and
  the empty v0.2.0 release was published anyway;
- nothing in continuous integration published to PyPI. The 0.2.0 sdist and
  wheel were uploaded by hand from `make build-release`, so PyPI held artefacts
  the GitHub Release lacked and no run had attested either.

The project is pure Python. A checked-in cibuildwheel workflow and action
existed but nothing called them.

Two constraints make a single linear job insufficient. Rebuilt distributions
are not byte-identical across runs, so a re-pushed tag cannot assume a second
build matches the first. PyPI refuses a second upload of an existing filename,
and the GitHub Release must not replace an asset a consumer may already have
downloaded and verified.

## Decision drivers

- No artefact may be silently replaced once published.
- A re-pushed tag must be safe to run again without manual cleanup, except
  when the destinations already disagree, which must fail loudly.
- The job holding the PyPI Trusted Publishing credential must resolve no
  third-party code and hold no GitHub token.
- Each job's permissions must be the minimum it needs.
- A missing or incomplete source distribution must fail a pull request rather
  than a tag.
- Every published byte must be traceable to a workflow run of this repository.

## Options considered

1. **Keep `action-gh-release` and the piped `find` upload.** Simple, but the
   pipe hides a missing build, and a re-run can replace an asset with different
   bytes under the same filename.
2. **Fail the whole workflow on any re-run of a tag that has published
   anything.** Safe, but a partially completed run such as "PyPI succeeded, the
   GitHub upload did not" would need manual recovery for every artefact, not
   only the ones that actually disagree.
3. **Reconcile per filename, with a fixed precedence and no overwrites**
   (chosen). Each filename's canonical bytes are decided once, from whichever
   destination already holds it; each destination receives only the names it
   lacks; and the workflow fails only when the destinations already disagree on
   a name, which needs a human to remove the offending asset.

## Decision outcome

Adopt per-filename reconciliation with least-privilege jobs and
`permissions: {}` at the workflow's top level, so no job inherits a scope it
did not declare.

| Job               | Needs                                            | Permissions                                                | Responsibility                                                      |
| ----------------- | ------------------------------------------------ | ---------------------------------------------------------- | ------------------------------------------------------------------- |
| `check-version`   | none                                             | `contents: read`                                           | Compare the tag with `pyproject.toml`; report pre-release status.   |
| `build`           | none                                             | `contents: read`                                           | Build the sdist, then the wheel from it, through the shared action. |
| `attest`          | `check-version`, `build`                         | `contents: read`, `id-token: write`, `attestations: write` | Collect one sdist and every wheel, attest them, stage the bundle.   |
| `draft-release`   | `check-version`, `attest`                        | `contents: write`, `attestations: read`                    | Create or reuse the draft; snapshot and verify carried assets.      |
| `publish-pypi`    | `attest`, `draft-release`                        | `contents: read`, `id-token: write`                        | Publish only the names PyPI lacks, under the `pypi` environment.    |
| `publish-release` | `check-version`, `draft-release`, `publish-pypi` | `contents: write`, `attestations: read`                    | Upload missing release assets, verify both destinations, publish.   |

_Table 1: Release job responsibilities and token scopes._

`build` deliberately runs beside `check-version` rather than behind it, so the
build lane never waits on a hosted job; every job that attests, uploads, or
releases waits on the version check instead.

### Building and attesting

The shared `.github/actions/pure-python-wheel` action runs plain `uv build`,
which builds the source distribution first and then the wheel from that source
distribution. Every build therefore proves the sdist is buildable. The same
action runs in the `build-dist` pull-request job, so a missing or incomplete
sdist fails continuous integration rather than the tag.

`scripts/check_dist.py` requires exactly one sdist and at least one wheel,
requires the sdist to carry the manual-page sources and Docutils configuration,
refuses generated `.roff` pages in the sdist, and requires every wheel to carry
one generated page per console script declared in `pyproject.toml`.

`attest` collects every wheel and exactly one sdist from the downloaded build
artefacts, attests them with `actions/attest-build-provenance`, and hands the
files and the run-unique Sigstore bundle
`git-donkey-<tag>-run<run_id>-<attempt>.sigstore.json` on as `release-dist`.
Every later job ships those bytes rather than rebuilding.

### Reconciliation and precedence

`scripts/release_assets.py` decides, once per filename, which destination's
bytes are canonical: PyPI's, if PyPI already lists the name; otherwise the
GitHub Release's, if it already has the name; otherwise this run's freshly
built and attested file. Each destination then receives only the names it still
lacks. There is no `--clobber` anywhere in the workflow.

A carried GitHub asset is used for PyPI only when GitHub reports its SHA-256
digest and a verifier record binds that exact filename and digest, this
repository, `release.yml`, the tag ref, and the triggering commit. A differing
unverified asset stops staging before either destination changes. In the other
direction, a PyPI file uploaded to the GitHub Release must first match PyPI's
index digest and then match an attestation for the same identity and the SLSA
v1 predicate. The commit binding means a moved tag cannot carry bytes built for
a different commit into PyPI, while a rerun of the same tag at the same commit
still accepts an interrupted attempt's assets.

`publish-release` stages only names that PyPI's index lists. A name the index
omits means the index has not caught up or the upload did not happen; the step
fails and a rerun waits for the index rather than uploading this run's rebuild
in place of bytes PyPI serves.

### No-overwrite verification and manual recovery

Before `publish-release` makes the release visible, it re-reads both the PyPI
index and the release's asset list and confirms every artefact name has an
identical SHA-256 digest on both sides and that the release carries a Sigstore
bundle. A name that already differs between the destinations, or PyPI bytes
that no run attested for this tag and commit, fails the job and leaves the
release a draft.

Recovery for a disagreeing GitHub asset is deliberately manual: an
administrator deletes the offending asset from the GitHub Release, because
PyPI's published files can never be deleted, and a later run reconciles the
remaining names. Bytes already on PyPI cannot be replaced, so if PyPI holds
bytes no run attested, a new version must be published.

### Rerun adaptations

Two decisions make a fully published re-run a no-op, beyond the reconciliation
PyPI already requires:

- `stage-github` copies this run's bundle only when a distribution needs
  uploading or the release has no bundle. A rerun that finds every name
  published and a bundle attached stages nothing, so the upload step is skipped.
- `publish-release` reads the release's `isDraft` and `isPrerelease` state and
  edits it only when the release is still a draft or its pre-release flag
  differs from the version check's answer.

### Least privilege and the `pypi` environment

`publish-pypi` runs under the `pypi` GitHub environment, restricted to tags
matching `v*.*.*`, and holds only `contents: read` for its sparse checkout of
`scripts/` plus `id-token: write` for Trusted Publishing. It never receives
`contents: write`, so it cannot create, edit, or upload to the GitHub Release;
that stays in `draft-release` and `publish-release`. `publish-pypi` receives no
`GH_TOKEN`, and its release scripts use only the Python standard library, so no
third-party package is resolved while the OIDC credential is live.

### Queued concurrency

Runs for one tag share the concurrency group `release-${{ github.ref }}` with
`cancel-in-progress: false`. A re-pushed tag queues behind the running release
rather than cancelling it mid-upload, which would leave a destination holding
only some of the run's names.

### Telemetry

Not adopted. cuprum's `release-telemetry` action writes to the sink defined by
its ADR-014; git-donkey has no telemetry sink, credential, or consumer for
those records. The release workflow records outcomes through its job results
and step annotations instead.

### Native build path

Removed. The project is pure Python, nothing called the checked-in cibuildwheel
workflow or its action, and that matrix would publish wheels this package's
build backend does not produce.

### Stdlib-only release helpers

`scripts/release_assets_cli.py`, `scripts/release_version.py`,
`scripts/release_gh_version.py`, `scripts/release_assets.py`,
`scripts/release_github_upload.py`, and `scripts/release_provenance.py` use
only the Python standard library. They run under the runner's preinstalled
`python3`, not through `uv run`, because `publish-pypi` holds the PyPI OIDC
credential and must not resolve any third-party package while it is live. This
is a deliberate, documented exception to
[scripting standards](../scripting-standards.md). Every network call stays in
the workflow's own `curl` and `gh` steps.

### The 0.2.0 restriction

PyPI's 0.2.0 sdist and wheel were uploaded by hand before this workflow
existed, so no run attested them. A `v0.2.0` tag would fail `publish-release`'s
provenance gate when it tries to carry PyPI's bytes to the GitHub Release, and
the release would remain a draft. The first automated release must therefore
carry a version above 0.2.0.

## Consequences

- A re-pushed tag is safe to run again: every destination converges on the
  same bytes per filename, and nothing already published is ever overwritten.
- A pull request whose build lacks an sdist fails before the tag exists.
- The GitHub Release receives exactly the bytes PyPI holds, plus the Sigstore
  bundle, and stays a draft until both destinations agree.
- `publish-pypi` holds no GitHub token and no attestation permission.
- Publication history is observable through the workflow run, the GitHub
  Release, and the repository's attestation store, without a new service.

## Known risks and limitations

- The reconciliation logic is a single point of failure for correctness. The
  checked-in-step tests execute the workflow's own scripts against scratch
  directories and fake `gh` and `curl` state, and the contract tests pin the
  job scopes, environment, concurrency, ordering, and action pins.
- The PyPI simple index is the source of truth for what PyPI already holds. An
  index outage that returns a non-404, non-2xx status fails the phase rather
  than guessing, so a PyPI-side incident can block a release until it resolves.
- PyPI's existing 0.2.0 files carry no provenance this repository attests, so
  they cannot be carried to the GitHub Release. The next release must use a
  higher version.
- When a release is completed from bytes carried over from an earlier run, the
  attached bundle is the completing run's bundle, which attests that run's
  build. The attestation store remains the authoritative record for every
  published file, and verifying a carried file against the attached bundle
  fails closed rather than passing incorrectly. Extending the bundle to cover
  carried bytes is tracked in [issue #203][bundle-coverage].

[bundle-coverage]: https://github.com/leynos/git-donkey/issues/203
