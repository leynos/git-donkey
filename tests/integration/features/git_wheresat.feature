Feature: Locate the replay boundary for a squash-merged parent

  Background:
    Given a child branch stacked on a parent branch
    And the parent pull request was squash-merged into the trunk

  Scenario: Established by a birth stack record
    Given a stack record naming the inherited boundary
    When I run git wheresat
    Then the report names the recorded commit as the exclusive replay boundary
    And the report proposes a backup ref and a rebase command with full object IDs
    And the command exits with status 0

  Scenario: Established by pull request head
    Given no stack record
    And the parent pull request head is an ancestor of the child branch
    When I run git wheresat, naming the parent pull request
    Then the report names the pull request head as the exclusive replay boundary
    And the report cites pull request head ancestry as the establishing evidence
    And the command exits with status 0

  Scenario: Refusal after a rewritten parent
    Given no stack record
    And the parent branch was rebased before it was merged
    And no surviving ref or reflog records the historical parent tip
    When I run git wheresat, naming the parent pull request, with deep scanning enabled
    Then the report names the parent-history-intact gate as the reason
    And the report proposes no rebase command
    And the command exits with status 1

  Scenario: Two inferred candidates remain unresolved
    Given no stack record
    And the parent branch was rebased before it was merged
    And only content-comparison evidence remains
    And two distinct commits match the squashed parent change
    When I run git wheresat with deep scanning enabled
    Then the report lists both candidates with their evidence tier
    And the report states the unresolved distinction between them
    And the report proposes no rebase command
    And the command exits with status 1

  Scenario: The parent pull request was opened from a fork
    Given the parent pull request was opened from a fork of the child repository
    When I run git wheresat, naming the parent pull request
    Then the pull request head is fetched from the fork rather than from origin
    And the report names the pull request head as the exclusive replay boundary
    And the command exits with status 0

  Scenario: Shallow history cannot answer the ancestry question
    Given the repository history is shallow
    When I run git wheresat
    Then the report states that the ancestry check was indeterminate
    And the report does not state that the boundary is not an ancestor
    And the command exits with status 3

  Scenario: The run leaves the repository unchanged
    Given a stack record naming the inherited boundary
    When I run git wheresat, naming the parent pull request
    Then no branch, tag, remote-tracking ref, index entry, or tracked file changes
    And the only new refs are under the evidence namespace
