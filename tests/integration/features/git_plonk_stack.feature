Feature: Preserve stack evidence through cleanup

  Scenario: Deleting a branch leaves a tombstone
    Given a completed git donkey worktree for a branch with a stack record
    When I run git plonk in hard mode
    Then the branch is deleted
    And a tombstone names the tip the branch had
    And no live stack record remains for that branch

  Scenario: A branch with no record of its own is still entombed
    Given a completed git donkey worktree for a branch created from the trunk
    And a child branch stacked on it
    When I run git plonk in hard mode
    Then the branch is deleted
    And a tombstone names the tip the branch had

  Scenario: A dry run reports the tombstone without writing it
    Given a completed git donkey worktree for a branch with a stack record
    When I run git plonk in hard mode as a dry run
    Then the summary names the tombstone it would write
    And no tombstone is created
    And the stack record is unchanged

  Scenario: An orphaned record is swept
    Given a stack record whose branch was deleted outside git plonk
    When I run git plonk in default mode
    Then the orphaned record becomes a tombstone
    And the summary names the record it swept

  Scenario: An orphan Git deleted alone is cleared without a tombstone
    Given a stack record whose branch was deleted with plain git
    When I run git plonk in default mode
    Then no tombstone is created
    And the orphaned record is cleared
    And the summary reports the orphan as having no tip to preserve

  Scenario: An expired tombstone is pruned
    Given a tombstone older than the retention window
    When I run git plonk in default mode
    Then the tombstone is deleted
    And the summary names the tombstone it pruned
