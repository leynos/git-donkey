Feature: Decide which git donkey worktrees a completion marker completes

  Scenario: A df12 roadmap branch completes on its dotted id
    Given a repository with a git donkey worktree on branch "roadmap-2-2-1"
    And a trunk commit message "Complete roadmap work (2.2.1)"
    When I run git plonk in default mode
    Then the completed worktree is removed
    And the completed branch remains
    And git plonk reports the removal as "completed by marker (2.2.1)"

  Scenario: A df12 roadmap addendum completes on its parent id
    Given a repository with a git donkey worktree on branch "roadmap-2-2-1-addendum"
    And a trunk commit message "Complete roadmap addendum (2.2.1)"
    When I run git plonk in default mode
    Then the completed worktree is removed
    And git plonk reports the removal as "completed by marker (2.2.1)"

  Scenario: A squash merge closing the issue completes a worktree
    Given a repository with a git donkey worktree on branch "issue-62-polymer-corrections"
    And a trunk commit message "Fix polymer corrections (#63)\n\nCloses #62"
    When I run git plonk in default mode
    Then the completed worktree is removed
    And the completed branch remains
    And git plonk reports the removal as "completed by closing keyword 'Closes #62'"

  Scenario: A closing keyword naming another repository does not complete a worktree
    Given a repository with a git donkey worktree on branch "issue-66-polymer-corrections"
    And a trunk commit message "Closes leynos/other#66"
    When I run git plonk in default mode
    Then the completed worktree remains
    And the completed branch remains
    And git plonk reports the completed worktree as unverified with "no completion signal found"

  Scenario: A classic merge commit naming the branch completes a worktree
    Given a repository with a git donkey worktree on branch "issue-64-polymer-corrections"
    And a trunk commit message "Merge pull request #65 from leynos/issue-64-polymer-corrections"
    When I run git plonk in default mode
    Then the completed worktree is removed
    And the completed branch remains
    And git plonk reports the removal as "completed by merge commit leynos/issue-64-polymer-corrections"

  Scenario: A merge commit naming a longer branch does not complete a worktree
    Given a repository with a git donkey worktree on branch "issue-67-polymer-corrections"
    And a trunk commit message "Merge pull request #68 from leynos/issue-67-polymer-corrections-extra"
    When I run git plonk in default mode
    Then the completed worktree remains
    And the completed branch remains
    And git plonk reports the completed worktree as unverified with "no completion signal found"

  Scenario: A legacy roadmap branch ending at its task id completes
    Given a repository with a git donkey worktree on branch "road-1-2-3a-4"
    And a trunk commit message "Complete legacy roadmap work (road.1.2.3a.4)"
    When I run git plonk in default mode
    Then the completed worktree is removed
    And git plonk reports the removal as "completed by marker (road.1.2.3a.4)"

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
