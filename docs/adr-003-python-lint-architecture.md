# Architectural decision record (ADR) 003: Python lint architecture

## Status

Accepted on 2026-08-23. `make lint` runs four complementary Python lint tiers,
with Skylos as the blocking production dead-code detector.

## Date

2026-08-23.

## Context and Problem Statement

`git-donkey` already combines Ruff, `interrogate`, and `pyscn` in one local and
continuous-integration lint target. Those tools respectively cover fast source
and style feedback, docstring completeness, and static analysis. They do not
provide a strict production-only dead-code gate.

Skylos provides that final check, but its analysis is only reliable when its
own runtime Abstract Syntax Tree (AST) understands the project's syntax. It
also distinguishes typed entry points for implicit callers from documented
allow-list exceptions. The lint architecture needs a reproducible invocation
that preserves those boundaries without adopting benchmark infrastructure.

## Decision Drivers

- Keep one blocking lint target for local development and continuous
  integration.
- Detect production dead code without test-only references keeping it live.
- Prevent parser-version drift from creating phantom findings.
- Make every exception narrow, explained, and reviewable.

## Decision Outcome / Proposed Direction

`make lint` runs these Python lint tiers in order:

1. Ruff for fast source and docstring-style checks.
2. `interrogate` for docstring coverage.
3. `pyscn` for static analysis.
4. Skylos for strict production dead-code detection.

The Makefile defines a command-only `SKYLOS_CLI` macro that pins Skylos and
Python 3.14. The `SKYLOS` macro adds scan-specific options such as
`--config-file`; the production gate scans `git_donkey`, explicitly excludes
`tests`, and enables Skylos gate mode. Python 3.14 is required because Skylos
parses source using that runtime's AST, preventing newer syntax from becoming a
phantom dead-code finding.

When an implicit runtime caller is verified, a typed Skylos entry-point rule is
preferred. A documented allow-list exception is permitted only where that rule
cannot model the boundary, and it must include a caller-specific reason.
`make skylos-allow` accepts only non-whitespace `SYMBOL` and `REASON` values,
returning exit status 2 before Skylos runs when either value is absent.

## Consequences

- `make lint` and continuous integration block unexplained production dead
  code.
- Test-only references cannot suppress a production finding.
- The Skylos and Makeutil versions are explicit contracts that require a
  deliberate update.
- Contributors must investigate every finding instead of applying broad
  allow-list entries.
