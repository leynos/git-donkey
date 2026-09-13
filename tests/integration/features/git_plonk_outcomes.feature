Feature: Report what a git plonk sweep did and did not do

  Scenario: Default mode skips a completed worktree whose directory is missing
    Given a repository with a completed git donkey worktree whose directory is missing
    When I run git plonk in default mode
    Then git plonk reports the completed worktree as missing
    And the completed branch remains

  Scenario: Hard mode skips a completed worktree whose directory is missing
    Given a repository with a completed git donkey worktree whose directory is missing
    When I run git plonk in hard mode
    Then git plonk reports the completed worktree as missing
    And the completed branch remains

  Scenario: Default dry-run previews the clean worktree and reports the dirty one
    Given a repository with one clean and one dirty completed git donkey worktree
    When I run git plonk in default dry-run mode
    Then git plonk previews removing the clean worktree
    And git plonk reports the dirty worktree as skipped
    And both completed worktrees remain
    And both completed branches remain

  Scenario: Hard dry-run previews the clean branch and reports the dirty worktree
    Given a repository with one clean and one dirty completed git donkey worktree
    When I run git plonk in hard dry-run mode
    Then git plonk previews removing the clean worktree
    And git plonk previews deleting only the clean branch
    And git plonk reports the dirty worktree as skipped
    And both completed worktrees remain
    And both completed branches remain

  Scenario: A refused branch deletion is reported and fails the run
    Given a repository with a completed git donkey worktree whose branch cannot be deleted
    When I run git plonk in hard mode expecting failure
    Then the completed worktree is removed
    And the completed branch remains
    And git plonk reports the failed branch deletion

  Scenario: Skipping every candidate still reports the skips
    Given a repository with two dirty completed git donkey worktrees
    When I run git plonk in default mode
    Then git plonk reports both worktrees as skipped
    And git plonk does not report an empty sweep
