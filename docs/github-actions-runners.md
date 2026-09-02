# GitHub Actions runner decision

This document records the runner boundary for repository-owned GitHub Actions
jobs.

## Decision

Use the shared, uncached `namespace-profile-default` Namespace profile for
repository-owned Linux jobs. The profile is Ubuntu 22.04 on amd64 with 4 vCPU
and 16 GB of memory, and deliberately has no repository cache volume during the
pilot.

Keep the reusable wheel-building workflow's fixed GitHub-hosted multi-platform
matrix. It owns the Linux, Windows, macOS, and architecture-specific runners
as part of the native wheel build contract; callers provide only its declared
inputs, and must not replace the matrix with the shared Linux profile.

Declare only the permissions required by each workflow. The CI lint-and-test
job therefore grants `contents: read`; the release workflow retains
`contents: write` because it publishes release artefacts.

The actionlint configuration registers both `namespace-profile-default` and
`namespace-profile-default-arm64` so workflow linting accepts the estate's
runner labels. The current repository-owned jobs use the amd64 label; the arm64
label is registered for workflows that adopt it later.

## Rationale

The shared profile gives repository-owned Linux jobs a consistent execution
environment while avoiding a repository-specific cache volume during the pilot.
The reusable workflow owns its wheel matrix because native extension builds need
platform and architecture-specific runners that the shared amd64 profile cannot
provide. Callers provide only the workflow's declared inputs.
