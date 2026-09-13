Feature: Choose which worktrees a git plonk sweep reaches

  Scenario: Default mode ignores a completed worktree outside the git donkey root
    Given a repository with a completed worktree outside the git donkey root
    When I run git plonk in default mode
    Then the outside worktree remains
    And the outside branch remains
    And git plonk reports no matching worktrees

  Scenario: Hard mode ignores a completed worktree outside the git donkey root
    Given a repository with a completed worktree outside the git donkey root
    When I run git plonk in hard mode
    Then the outside worktree remains
    And the outside branch remains
    And git plonk reports no matching worktrees

  Scenario: A manually created worktree under the git donkey root is cleaned
    Given a repository with a manually created completed worktree under the git donkey root
    When I run git plonk in default mode
    Then the completed worktree is removed

  Scenario: Hard mode keeps the remote branch of a completed worktree
    Given a repository with a completed git donkey worktree pushed to the remote
    When I run git plonk in hard mode
    Then the completed worktree is removed
    And the completed branch is deleted
    And the remote branch remains
    And the remote-tracking branch remains

  Scenario: Soft mode cleans the worktree it is invoked from
    Given a repository with a generated directory in the invoking git donkey worktree
    When I run git plonk in soft mode
    Then the generated directory is removed
    And the worktree remains
    And the branch remains

  Scenario: Soft mode removes tracked content under a generated directory name
    Given a repository with tracked content under a generated directory name
    When I run git plonk in soft mode
    Then the generated directory is removed
    And the worktree remains
    And the worktree has uncommitted deletions

  Scenario: Soft mode unlinks a symlinked generated directory
    Given a repository with a symlinked generated directory
    When I run git plonk in soft mode
    Then the generated directory is removed
    And the symlink destination remains
    And the worktree remains
