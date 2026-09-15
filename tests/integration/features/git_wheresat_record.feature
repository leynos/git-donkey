Feature: Refresh a stack record

  Scenario: Anchoring a record whose anchor ref is gone
    Given a child branch with a stack record whose anchor ref is gone
    When I run git wheresat with recording enabled
    Then git wheresat succeeds
    And the stack-base ref names the boundary the record attests
    And the record preserves the parent it was born with
    And the record names the child tip the run was made at
    And the record names the refresh as its evidence

  Scenario: Refusing to overwrite an existing record
    Given a child branch with an existing stack record
    When I run git wheresat with recording enabled
    Then the existing record is unchanged
    And the command reports that an expected old object ID is required
    And the command exits with status 2

  Scenario: Refreshing a record with the expected old value
    Given a child branch with an existing stack record
    When I run git wheresat with recording enabled and the expected old value
    Then git wheresat succeeds
    And the stack-base ref names the boundary the record attests
    And the record preserves the parent it was born with
    And the record names the child tip the run was made at
    And the record names the refresh as its evidence

  Scenario: Refusing to record an unresolved result
    Given a stacked checkout whose parent cannot be replayed onto itself
    When I run git wheresat with recording enabled for parent
    Then no stack-base ref is created for parent
    And the existing record is unchanged
    And the command reports that nothing was recorded
    And the command exits with status 1
