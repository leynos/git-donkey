git-donkey-template
===================
Locate and create a repository's template overlay directory
----------------------------------------------------------

:Manual section: 1
:Manual group: Git-donkey manual

SYNOPSIS
========

**git donkey-template**

**git-donkey-template**

DESCRIPTION
===========

Display the template overlay directory for the current repository and create
it when it does not exist.
Run the command inside a Git repository.
Files placed in this directory are copied automatically into new worktrees
created by **git-donkey**\ (1).
Existing destination files receive a warning and are overwritten.

The directory is specific to the repository's remote URL.
Different remote URLs produce different template directories.
When several remotes exist and none is named ``origin``, report an error
rather than choose an ambiguous remote.
Rename the preferred remote to ``origin`` or remove the extra remotes.

OPTIONS
=======

-h, --help
    Display command-line help and exit.

--version
    Display the installed version and exit.

FILES
=====

The platform-specific user data directory contains
``git-donkey/template/<repo-url-slug>``.
The slug combines readable slugified text with an Adler-32 checksum.

On Linux, the base directory is ``$XDG_DATA_HOME``, defaulting to
``~/.local/share``.
On macOS, the base directory is ``~/Library/Application Support``.
Use the printed path instead of reconstructing the slug or platform path.

EXAMPLES
========

Display and create the overlay directory::

    cd ~/projects/example
    git donkey-template

Place files such as ``.editorconfig`` or ``.vscode/settings.json`` in the
printed directory before creating another worktree with ``git donkey``.

SEE ALSO
========

**git**\ (1), **git-donkey**\ (1), **git-worktree**\ (1)
