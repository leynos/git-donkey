# git-fafo trusted and adoptable scaffolds

This document records the design decisions behind trusted Copier scaffolds,
empty-project creation, and adoption of existing GitHub repositories in
`git-fafo`.

## Context

`git-fafo` creates a GitHub repository, scaffolds local files, initializes Git,
and pushes the first branch. The workflow now supports three additional cases:

- Passing Copier's `--trust` option when the caller explicitly opts in.
- Creating an empty repository when no language is provided.
- Adopting an existing GitHub repository when it is empty enough to be safe.

These cases are related because they change when local side effects happen. A
trusted Copier template can run tasks, so remote adoption must be validated
before scaffolding starts.

## Decisions

### Trust is template-scoped

`--trust` is passed only to Copier-backed scaffolds. When no language is
provided, `git-fafo` skips Copier entirely and creates the target directory
directly. This keeps trust attached to the only operation that can use it.

### Missing language means an empty scaffold

An omitted language creates the target directory, initializes Git, makes an
empty initial commit, and pushes `main`. This path is useful when the
repository should exist before language-specific files are known.

The direct directory creation handles `FileExistsError` as a conflict, so
concurrent runs fail with the same user-facing category as an already-existing
path.

### Existing remotes require explicit adoption

If GitHub reports that the repository already exists, `git-fafo` requires
interactive confirmation or `--yes`. This prevents accidental writes to an
existing repository whose name happens to match the requested scaffold.

Confirmation alone is not sufficient. The remote is classified before local
scaffolding begins, and adoption is accepted only when the remote has no refs
or one branch containing exactly one empty initial commit.

### Non-empty remotes are rejected before scaffolding

Remote classification rejects:

- Any tag.
- More than one branch head.
- A branch with more than one commit.
- A single initial commit that changes files.

The validation runs in a temporary Git repository so a rejection leaves no
local scaffold directory behind. This matters most for trusted Copier
templates, where task execution could otherwise happen before the conflict is
reported.

### Operational diagnostics stay inside logging

The adoption classifier logs acceptance and rejection reasons with stable
reason values such as `zero_commit_remote`, `empty_initial_commit`,
`tags_present`, `multiple_heads`, and `non_empty_history`. The project does not
currently have a metrics backend, so these reason values are the integration
point for future metrics rather than an ad hoc counter implementation.
