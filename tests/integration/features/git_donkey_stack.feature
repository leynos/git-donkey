Feature: Record a branch's stack parent at birth

  Scenario: A stacked branch records its parent
    Given a repository whose trunk is main
    And a feature branch parent one commit ahead of main
    When I create a branch child from parent with git donkey
    Then git donkey succeeds
    And the branch configuration for child names parent as its stack parent
    And the stack-base anchor for child names the tip parent had
    And the recorded base equals the commit git donkey froze

  Scenario: A trunk branch records nothing
    Given a repository whose trunk is main
    When I create a branch solo from main with git donkey
    Then git donkey succeeds
    And no stack record exists for solo

  Scenario: An implicit base records nothing
    Given a repository whose trunk is main
    When I create a branch solo with git donkey naming no base
    Then git donkey succeeds
    And no stack record exists for solo

  Scenario: Recording does not disturb branch tracking
    Given a repository whose trunk is main
    And a feature branch parent one commit ahead of main
    When I create a branch child from parent with git donkey
    Then git donkey succeeds
    And child has no upstream tracking configuration
