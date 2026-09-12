Feature: Clean git donkey worktrees

  Scenario: Default mode removes completed worktrees only
    Given a repository with completed and active git donkey worktrees
    When I run git plonk in default mode
    Then the completed worktree is removed
    And the active worktree remains
    And the completed branch remains

  Scenario: Default dry-run reports completed worktree cleanup without branches
    Given a repository with completed and active git donkey worktrees
    When I run git plonk in default dry-run mode
    Then the completed worktree remains
    And the active worktree remains
    And the completed branch remains
    And git plonk reports planned default worktree cleanup only

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

  Scenario: A staged change is skipped without stopping the sweep
    Given a repository with a completed git donkey worktree holding a staged change beside a clean one
    When I run git plonk in default mode
    Then the completed worktree is removed
    And the dirty completed worktree remains
    And git plonk reports the dirty worktree as skipped

  Scenario: An untracked file keeps a completed worktree and its branch
    Given a repository with a completed git donkey worktree holding an untracked file
    When I run git plonk in default mode
    Then the completed worktree remains
    And the completed branch remains
    And git plonk reports the completed worktree as skipped

  Scenario: A tracked modification keeps a completed worktree and its branch
    Given a repository with a completed git donkey worktree holding a tracked modification
    When I run git plonk in default mode
    Then the completed worktree remains
    And the completed branch remains
    And git plonk reports the completed worktree as skipped

  Scenario: Hard mode keeps the branch of a skipped worktree
    Given a repository with a completed git donkey worktree holding a tracked modification
    When I run git plonk in hard mode
    Then the completed worktree remains
    And the completed branch remains
    And git plonk reports the completed worktree as skipped

  Scenario: Ignored build output does not protect a completed worktree
    Given a repository with a completed git donkey worktree holding ignored build output
    When I run git plonk in default mode
    Then the completed worktree is removed
    And the completed branch remains
