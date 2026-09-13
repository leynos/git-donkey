Feature: Resolve the trunk git plonk judges completion against

  Scenario: A non-main advertised default supplies the completion history
    Given a repository whose advertised trunk carries the completion marker
    When I run git plonk in default mode
    Then the completed worktree is removed

  Scenario: A marker on local main alone does not complete a worktree
    Given a repository whose advertised trunk lacks a marker local main carries
    When I run git plonk in default mode
    Then the completed worktree remains
    And the completed branch remains

  Scenario: A remote advertising no default branch stops the sweep
    Given a repository whose remote advertises no default branch
    When git plonk fails in default mode
    Then git plonk reports a missing advertised default branch
    And the completed worktree remains
    And the completed branch remains

  Scenario: An unreachable remote stops the sweep
    Given a repository whose remote has been deleted
    When git plonk fails in default mode
    Then the completed worktree remains
    And the completed branch remains

  Scenario: A dry run fetches the advertised trunk before planning
    Given a repository whose remote carries a marker the local clone has not fetched
    When I run git plonk in default dry-run mode
    Then git plonk plans to remove the completed worktree
    And the completed worktree remains

  Scenario: Default cleanup never consults the GitHub API
    Given a repository with a completed git donkey worktree and recorded GitHub API traffic
    When I run git plonk in default mode
    Then the completed worktree is removed
    And no GitHub API request was made

  Scenario: Hard cleanup never consults the GitHub API
    Given a repository with a completed git donkey worktree and recorded GitHub API traffic
    When I run git plonk in hard mode
    Then the completed worktree is removed
    And the completed branch is deleted
    And no GitHub API request was made
