Feature: Reuse branches, refuse conflicts, and overlay templates

  Scenario: A supplied base does not move an existing local branch
    Given a repository with an existing local branch behind a newer main
    When I run git donkey with main as the base
    Then git donkey succeeds
    And the new worktree starts at the existing branch tip

  Scenario: A same-named remote-only branch gains a local tracking branch
    Given a repository with a branch that exists only on the remote
    When I run git donkey with the remote default base
    Then git donkey succeeds
    And the local branch tracks its remote counterpart
    And the new worktree starts at the remote branch tip

  Scenario: A local-only branch without an upstream cannot be reused
    Given a repository with a local-only branch that was never pushed
    When I run git donkey with the remote default base
    Then git donkey fails with code 1 and reports "remote branch not found"
    And no worktree is created
    And the branch is left at its original tip

  Scenario: A branch already checked out elsewhere is refused
    Given a repository whose branch is checked out in another worktree
    When I run git donkey with the remote default base
    Then git donkey fails with code 1 and reports "already checked out at"
    And no worktree is created

  Scenario: An occupied target path is refused
    Given a repository whose target worktree path is already occupied
    When I run git donkey with the remote default base
    Then git donkey fails with code 1 and reports "target path already exists"
    And the occupying file is untouched
    And no branch is created

  Scenario: A detached HEAD in the calling checkout is refused
    Given a repository with a detached HEAD in the calling checkout
    When I run git donkey with the remote default base
    Then git donkey fails with code 2 and reports "HEAD is detached"
    And no branch is created

  Scenario: Branch slashes become nested worktree path components
    Given a repository and a branch name containing nested path components
    When I run git donkey with the remote default base
    Then git donkey succeeds
    And the worktree is registered for the nested branch at the nested path
    And the new branch has no upstream

  Scenario: A template overlay overwrites a tracked file with a warning
    Given a repository with a template that overwrites a tracked file
    When I run git donkey with the remote default base
    Then git donkey succeeds
    And git donkey reports "Warning: file already exists, overwriting: README.md"
    And the new worktree holds the template content
    And the new worktree is dirty while the calling checkout stays clean
