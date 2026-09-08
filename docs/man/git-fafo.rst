git-fafo
========
Scaffold and publish a GitHub repository
--------------------------------------

:Manual section: 1
:Manual group: Git-donkey manual

SYNOPSIS
========

**git fafo** *REPO_NAME* [*LANGUAGE*] [**--trust**] [**--yes**]

**git-fafo** *REPO_NAME* [*LANGUAGE*] [**--trust**] [**--yes**]

DESCRIPTION
===========

Create a local project, initialize Git, create a GitHub repository, and push
the initial commit.
With *LANGUAGE*, scaffold using Copier and the current GitHub account's
``agent-template-<language>`` repository.
Without *LANGUAGE*, create an empty repository with an empty initial commit.
An existing local target directory is a conflict.

An existing GitHub repository can be adopted only when it has no commits or
only an empty initial commit.
Prompt before adoption unless **--yes** was supplied.
Never adopt a repository that contains real content.

The command requires ``git`` and, for template-backed scaffolds, ``copier``.
Authentication uses an environment token, a cached token, or interactive
OAuth device flow.
Do not enable trusted Copier tasks without reviewing the template source.

OPTIONS
=======

REPO_NAME
    Name of the local project directory and GitHub repository.

LANGUAGE
    Template suffix used in ``agent-template-<language>``.
    Omit it to create an empty project without Copier.

--trust
    Allow the selected Copier template to run trusted features, including
    tasks that execute commands.
    This option only affects template-backed scaffolds.

-y, --yes
    Confirm adoption of an eligible existing GitHub repository without a
    prompt; this does not bypass the remote-content safety check.

-h, --help
    Display command-line help and exit.

--version
    Display the installed version and exit.

ENVIRONMENT
===========

GITHUB_TOKEN, GH_TOKEN
    GitHub access token; ``GITHUB_TOKEN`` takes precedence.

GIT_DONKEY_GITHUB_CLIENT_ID
    Override the OAuth client ID used for interactive device flow.
    The default is ``Ov23liD2cKOAh7xmpXKR``.

GIT_DONKEY_CREDENTIALS_FILE
    Override the token-cache path, which defaults to
    ``~/.config/git-donkey/github-token``.

EXAMPLES
========

Create an empty project::

    git fafo demo-repo

Scaffold from a Python template::

    git fafo demo-repo python

Adopt an eligible empty remote without prompting::

    git fafo demo-repo --yes

Allow tasks from a reviewed, trusted template::

    git fafo demo-repo python --trust

SEE ALSO
========

**git**\ (1), **git-donkey**\ (1)
