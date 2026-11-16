Feature: Clean up worktrees in a bare repository collection

  Scenario: Default mode removes a completed git donkey worktree
    Given a bare repository collection with a completed git donkey worktree
    When I run git plonk in the bare collection
    Then the completed git donkey worktree is removed
    And the completed session worktree remains

  Scenario: Hard mode never deletes a session branch
    Given a bare repository collection with a completed git donkey worktree
    When I run git plonk in hard mode in the bare collection
    Then the completed git donkey worktree is removed
    And the completed git donkey branch is deleted
    And the completed session worktree remains
    And the completed session branch remains

  Scenario: Soft mode leaves a session worktree's generated directories alone
    Given a bare repository collection with generated directories in every worktree
    When I run git plonk in soft mode in the bare collection
    Then the git donkey worktree's generated directory is removed
    And the session worktree's generated directory remains

  Scenario: The bare repository directory can run git plonk itself
    Given a bare repository collection with a completed git donkey worktree
    When I run git plonk from the bare directory
    Then the completed git donkey worktree is removed
    And the completed session worktree remains
