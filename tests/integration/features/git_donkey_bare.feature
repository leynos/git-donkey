Feature: Create worktrees in a bare repository collection

  Scenario: A linked worktree of a bare repository gains a sibling worktree root
    Given a bare repository collection entered through a linked worktree
    When I run git donkey with the remote default base
    Then git donkey succeeds
    And the new worktree is created under the bare repository's worktree root
    And the new worktree starts at the remote default tip
    And the session worktree directory holds only the session worktree

  Scenario: The bare repository directory can run git donkey itself
    Given a bare repository collection entered through the bare directory
    When I run git donkey with the remote default base
    Then git donkey succeeds
    And the new worktree is created under the bare repository's worktree root

  Scenario: A dot base selects the branch of the invoking linked worktree
    Given a bare repository collection whose session branch is ahead of the remote default
    When I run git donkey with the current branch as the base
    Then git donkey succeeds
    And the new worktree starts at the session branch tip

  Scenario: A configured worktree root replaces the default
    Given a bare repository collection with a configured worktree root
    When I run git donkey with the remote default base
    Then git donkey succeeds
    And the new worktree is created under the configured worktree root

  Scenario: A remote without a fetch refspec is refused
    Given a bare repository collection whose remote has no fetch refspec
    When I run git donkey with the remote default base
    Then git donkey fails with code 2 and reports "has no fetch refspec"
    And git donkey reports "git config remote.origin.fetch"
    And no worktree root is created
