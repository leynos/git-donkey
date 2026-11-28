Feature: Treat a deleted upstream as completion evidence

  Scenario: Default mode leaves a branch with a deleted upstream unverified
    Given a repository with a git donkey worktree whose upstream has been deleted
    When I run git plonk in default mode
    Then the completed worktree remains
    And the completed branch remains
    And git plonk reports the completed worktree as unverified with "no completion signal found"

  Scenario: The upstream-gone tier completes a branch with a deleted upstream
    Given a repository with a git donkey worktree whose upstream has been deleted
    When I run git plonk with the upstream-gone tier
    Then the completed worktree is removed
    And the completed branch remains
    And git plonk reports the removal as "completed by gone upstream branch"

  Scenario: A still-present upstream does not complete a branch
    Given a repository with a git donkey worktree whose upstream still exists
    When I run git plonk with the upstream-gone tier
    Then the completed worktree remains
    And the completed branch remains
    And git plonk reports the completed worktree as unverified with "upstream branch still exists"

  Scenario: A never-pushed branch does not complete a branch
    Given a repository with a git donkey worktree that was never pushed
    When I run git plonk with the upstream-gone tier
    Then the completed worktree remains
    And the completed branch remains
    And git plonk reports the completed worktree as unverified with "no upstream branch configured"