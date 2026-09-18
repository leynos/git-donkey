# Documentation index

This index summarizes the maintained project documentation.

- [Developer guide](developers-guide.md) records local tooling, module
  boundaries, test conventions, and the Python lint workflow.
- [ADR-003: Python lint architecture][adr-003] records the Skylos dead-code
  tier and its exception policy.
- [ADR-004: Shared stack records][adr-004] records the record format and
  lifecycle the three commands share.
- [ADR-005: Squash-restack evidence precedence][adr-005] records the evidence
  tiers, the gate conjunction, and the exit codes `git wheresat` uses.
- [Documentation style guide](documentation-style-guide.md) defines project
  documentation conventions.
- [Git FAFO adoption](git-fafo-adoption.md) explains existing-repository
  adoption.
- [Plonk cleanup policy](plonk-cleanup-policy.md) records which completed
  worktrees `git plonk` removes, which it skips, and how completion is judged.
- [Scripting standards](scripting-standards.md) records requirements for local
  automation.
- [Squash-restack boundary recovery](squash-restack-boundary-recovery.md)
  specifies how `git wheresat` derives the exclusive replay boundary.
- [The shared stack record](stack-records.md) specifies the record format and
  lifecycle that `git donkey`, `git plonk`, and `git wheresat` share.
- [Users' guide](users-guide.md) explains the public command-line workflows.
- [Worktree-management skill](../skill/git-donkey-worktrees/SKILL.md) provides
  an agent workflow for creation, reuse, stacked branches, and reviewed cleanup
  with `git donkey` and `git plonk`.
- [0.2.0 migration guide](v0-2-0-migration-guide.md) records the user-visible
  changes for users upgrading from 0.1.0, including the base-selection and
  cleanup changes and the new comparison commands.

[adr-003]: adr-003-python-lint-architecture.md
[adr-004]: adr-004-shared-stack-records.md
[adr-005]: adr-005-squash-restack-evidence-precedence.md
