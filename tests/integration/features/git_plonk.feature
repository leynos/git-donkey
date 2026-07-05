Feature: Clean git donkey worktrees

  Scenario: Default mode removes completed worktrees only
    Given a repository with completed and active git donkey worktrees
    When I run git plonk in default mode
    Then the completed worktree is removed
    And the active worktree remains
    And the completed branch remains

  Scenario: Soft mode removes generated directories without removing worktrees
    Given a repository with generated directories inside git donkey worktrees
    When I run git plonk in soft mode
    Then the generated directories are removed
    And the worktrees remain
    And the branches remain

  Scenario: Soft dry-run reports generated cleanup without removing anything
    Given a repository with generated directories inside git donkey worktrees
    When I run git plonk in soft dry-run mode
    Then the generated directories remain
    And the worktrees remain
    And the branches remain
    And git plonk reports planned generated path cleanup

  Scenario: Hard mode removes completed worktrees and branches
    Given a repository with a completed git donkey worktree
    When I run git plonk in hard mode
    Then the completed worktree is removed
    And the completed branch is deleted

  Scenario: Hard dry-run reports completed cleanup without removing anything
    Given a repository with a completed git donkey worktree
    When I run git plonk in hard dry-run mode
    Then the completed worktree remains
    And the completed branch remains
    And git plonk reports planned worktree and branch cleanup

  Scenario: Soft and hard modes cannot be combined
    Given a repository with completed and active git donkey worktrees
    When I run git plonk with soft and hard modes
    Then git plonk exits with a usage error
