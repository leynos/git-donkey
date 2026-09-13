Feature: Choose, fetch, and update git donkey worktree bases

  Scenario: An explicit local base still fetches from the remote first
    Given a repository whose remote has been deleted
    When I run git donkey with the current branch as the base
    Then git donkey fails with code 1 and reports "fetch failed"
    And no branch or worktree is created

  Scenario: The default and --no-pull both start at the remote tip
    Given a repository whose local base is behind its remote
    And a prompt that fails the test if it is asked
    When I run git donkey with --no-pull and again with no pull option
    Then both worktrees start at the remote tip

  Scenario Outline: Pull-mode options are rejected before the repository is read
    Given a directory that is not a Git repository
    When I run the git donkey CLI with <flags>
    Then the CLI reports a usage error about mutually exclusive options

    Examples:
      | flags                   |
      | --pull-ff --pull-rebase |
      | --no-pull --pull-ff     |

  Scenario: A non-interactive prompt skips an opted-in base update
    Given a repository whose local base is behind its remote
    And a non-interactive stdin
    When I run git donkey with --pull-ff and the current branch as the base
    Then git donkey succeeds
    And git donkey reports "Non-interactive"
    And the local base is left at its behind tip
    And the new worktree starts at the behind tip

  Scenario: The current branch base carries its commits but not its uncommitted files
    Given a repository with a committed change and uncommitted work in the calling checkout
    When I run git donkey with the current branch as the base
    Then git donkey succeeds
    And the new worktree starts at the calling checkout commit
    And the new worktree holds the committed content
    And the untracked file is absent from the new worktree
    And the calling checkout keeps its uncommitted work

  Scenario: Creating a worktree never calls the GitHub API
    Given a repository and a recorded GitHub API cassette
    When I run git donkey with the remote default base
    Then git donkey succeeds
    And no GitHub API request is made

  Scenario: The principal remote is the first configured remote
    Given a repository whose first configured remote is named upstream
    When I run git donkey with the remote default base
    Then git donkey succeeds
    And git donkey reports "Using remote: upstream"
    And the new worktree starts at the principal remote default tip
