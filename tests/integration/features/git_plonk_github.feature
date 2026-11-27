Feature: Resolve completion from merged GitHub pull requests

  Scenario: A merged pull request completes a descriptive branch
    Given a repository with a descriptive git donkey worktree on a GitHub remote
    And the gh stub reports a merged pull request targeting "main" for that branch
    When I run git plonk in default mode expecting a GitHub lookup
    Then the completed worktree is removed
    And the completed branch remains
    And git plonk reports the removal as "completed by merged pull request #61"
    And the GitHub lookup was attempted once

  Scenario: A failed lookup leaves the branch unverified
    Given a repository with a descriptive git donkey worktree on a GitHub remote
    And the gh stub fails
    When I run git plonk in default mode expecting a GitHub lookup
    Then the completed worktree remains
    And the completed branch remains
    And git plonk reports the completed worktree as unverified with "GitHub completion lookup failed"

  Scenario: A pull request targeting another base does not complete the branch
    Given a repository with a descriptive git donkey worktree on a GitHub remote
    And the gh stub reports a merged pull request targeting "release" for that branch
    When I run git plonk in default mode expecting a GitHub lookup
    Then the completed worktree remains
    And the completed branch remains
    And git plonk reports the completed worktree as unverified with "GitHub completion lookup failed"