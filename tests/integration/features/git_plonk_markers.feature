Feature: Decide which git donkey worktrees a completion marker completes

  Scenario: A marker in a commit body completes a worktree
    Given a repository whose trunk carries the commit message "Merge work\n\nCloses (#123)"
    When I run git plonk in default mode
    Then the completed worktree is removed

  Scenario: A different reference number does not complete a branch
    Given a repository whose trunk carries the commit message "Merge pull request (#124)"
    When I run git plonk in default mode
    Then the completed worktree remains
    And the completed branch remains

  Scenario: The dotted marker form completes a worktree
    Given a repository whose trunk carries the commit message "Merge pull request (#123.)"
    When I run git plonk in default mode
    Then the completed worktree is removed

  Scenario: Default mode leaves an unrecognised branch name alone
    Given a repository with an unrecognised git donkey worktree
    When I run git plonk in default mode
    Then the completed worktree remains
    And the completed branch remains

  Scenario: Hard mode leaves an unrecognised branch name alone
    Given a repository with an unrecognised git donkey worktree
    When I run git plonk in hard mode
    Then the completed worktree remains
    And the completed branch remains

  Scenario: One marker completes every worktree sharing it
    Given a repository with two git donkey worktrees sharing a completion marker
    When I run git plonk in default mode
    Then both worktrees sharing the marker are removed
    And git plonk reports both worktrees as removed

  Scenario: A committed change after the marker does not keep a worktree
    Given a repository with a completed git donkey worktree holding a later commit
    When I run git plonk in default mode
    Then the completed worktree is removed
    And the completed branch remains
    And the completed branch keeps its later commit

  Scenario: Hard mode deletes a completed branch holding an unmerged commit
    Given a repository with a completed git donkey worktree holding a later commit
    When I run git plonk in hard mode
    Then the completed worktree is removed
    And the completed branch is deleted
