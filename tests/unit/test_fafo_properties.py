"""Property tests for ``git-fafo`` scaffold invariants.

The example tests cover named workflows; this module checks broader input
spaces for pure command builders and stubbed adoption decisions. It depends on
Hypothesis only through the normal pytest runner and avoids external GitHub or
Git subprocesses.
"""

from __future__ import annotations

import dataclasses
import tempfile
import typing as typ
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from plumbum.commands.processes import ProcessExecutionError

from git_donkey import fafo, fafo_adoption

_SLUG_TEXT = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_",
    min_size=1,
    max_size=24,
)


@given(
    copier_path=_SLUG_TEXT,
    template=_SLUG_TEXT,
    repo_name=_SLUG_TEXT,
    trust=st.booleans(),
)
def test_copier_copy_command_keeps_trust_position(
    copier_path: str,
    template: str,
    repo_name: str,
    *,
    trust: bool,
) -> None:
    """Copier command construction should preserve argument ordering."""
    command = fafo._copier_copy_command(
        copier_path=copier_path,
        template=template,
        repo_name=repo_name,
        trust=trust,
    )

    assert command[:2] == [copier_path, "copy"]
    assert command[-2:] == [template, repo_name]
    assert ("--trust" in command) is trust
    if trust:
        assert command[2] == "--trust"


@given(owner=_SLUG_TEXT, language=st.none() | _SLUG_TEXT)
def test_template_for_language_maps_language_to_owner_template(
    owner: str,
    language: str | None,
) -> None:
    """Template resolution should be empty or owner/language-specific."""
    template = fafo._template_for_language(owner=owner, language=language)

    if language is None:
        assert template is None
    else:
        assert template == f"git@github.com:{owner}/agent-template-{language}"


@given(
    heads=st.lists(_SLUG_TEXT, max_size=4),
    tags=st.lists(_SLUG_TEXT, max_size=4),
)
def test_remote_ref_parser_classifies_heads_and_tags(
    heads: list[str],
    tags: list[str],
) -> None:
    """The ls-remote parser should ignore HEAD and peeled tag aliases."""
    lines = ["0" * 40 + "\tHEAD"]
    lines.extend(f"{index:040x}\trefs/heads/{head}" for index, head in enumerate(heads))
    lines.extend(f"{index:040x}\trefs/tags/{tag}" for index, tag in enumerate(tags))
    lines.extend(
        f"{index:040x}\trefs/tags/{tag}^{{}}" for index, tag in enumerate(tags)
    )

    parsed_heads, parsed_tags = fafo._heads_and_tags_from_ls_remote("\n".join(lines))

    assert parsed_heads == heads
    assert parsed_tags == tags


@dataclasses.dataclass
class _FakeGit:
    """Small plumbum-like Git command fake for adoption properties."""

    outputs: dict[tuple[object, ...], str]
    empty_commit: bool = True
    args: tuple[object, ...] = ()

    def __getitem__(self, args: object) -> _FakeGit:
        """Return a new fake command with appended arguments."""
        if isinstance(args, tuple):
            next_args = (*self.args, *args)
        else:
            next_args = (*self.args, args)
        return _FakeGit(self.outputs, self.empty_commit, next_args)

    def __call__(self) -> str:
        """Run the fake command and return configured stdout."""
        if self.args == (
            "diff-tree",
            "--quiet",
            "--exit-code",
            "--root",
            "origin/main",
        ):
            if not self.empty_commit:
                raise ProcessExecutionError(list(self.args), 1, "", "")
            return ""
        return self.outputs.get(self.args, "")


@given(
    extra_heads=st.lists(_SLUG_TEXT, min_size=1, max_size=3),
    tags=st.lists(_SLUG_TEXT, max_size=3),
    empty_commit=st.booleans(),
)
def test_remote_classification_rejects_non_adoptable_histories(
    extra_heads: list[str],
    tags: list[str],
    *,
    empty_commit: bool,
) -> None:
    """Remote adoption should reject tags, multiple heads, or real commits."""
    heads = ["main", *extra_heads]
    lines = [f"{index:040x}\trefs/heads/{head}" for index, head in enumerate(heads)]
    lines.extend(f"{index:040x}\trefs/tags/{tag}" for index, tag in enumerate(tags))
    git = _FakeGit(
        {
            ("ls-remote", "origin"): "\n".join(lines),
            ("rev-list", "--count", "origin/main"): "1\n",
        },
        empty_commit=empty_commit,
    )

    state = fafo._classify_remote(typ.cast("fafo._GitCommand", git))

    assert not state.adoptable
    assert state.reason in {"tags_present", "multiple_heads"}


@given(empty_commit=st.booleans())
def test_remote_classification_depends_on_single_head_commit_emptiness(
    *,
    empty_commit: bool,
) -> None:
    """A single-head remote is adoptable only for an empty initial commit."""
    git = _FakeGit(
        {
            ("ls-remote", "origin"): f"{1:040x}\trefs/heads/main",
            ("rev-list", "--count", "origin/main"): "1\n",
        },
        empty_commit=empty_commit,
    )

    state = fafo._classify_remote(typ.cast("fafo._GitCommand", git))

    assert state.adoptable is empty_commit


@given(yes=st.booleans(), prompt_response=st.booleans())
def test_adoption_confirmation_requires_yes_or_prompt_acceptance(
    *,
    yes: bool,
    prompt_response: bool,
) -> None:
    """Existing-repository adoption requires ``--yes`` or prompt acceptance."""
    prompts: list[bool] = []

    def _fake_prompt(*_: object, **__: object) -> bool:
        prompts.append(True)
        return prompt_response

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(fafo.helpers, "_prompt_yes_no", _fake_prompt)
        if yes or prompt_response:
            fafo._confirm_adopt_existing_repository(
                owner="octocat",
                repo_name="demo-repo",
                yes=yes,
            )
        else:
            with pytest.raises(SystemExit):
                fafo._confirm_adopt_existing_repository(
                    owner="octocat",
                    repo_name="demo-repo",
                    yes=yes,
                )

    assert bool(prompts) is not yes


def test_remote_adoption_inspection_failure_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote inspection failures should exit with a diagnostic failure."""

    def _fail_classify(_: object) -> fafo_adoption._RemoteState:
        raise ProcessExecutionError(["git", "ls-remote"], 128, "", "boom")

    monkeypatch.setattr(fafo_adoption, "_classify_remote", _fail_classify)

    with pytest.raises(SystemExit) as excinfo:
        fafo_adoption._assert_remote_adoptable(
            owner="octocat",
            repo_name="demo-repo",
            remote_url="git@example.invalid:octocat/demo-repo",
        )

    assert excinfo.value.code == 1


@given(repo_name=_SLUG_TEXT)
def test_empty_scaffold_creates_only_requested_directory(
    repo_name: str,
) -> None:
    """Empty scaffolding should create the requested directory without Copier."""
    with tempfile.TemporaryDirectory() as tmp, pytest.MonkeyPatch.context() as patch:
        tmp_path = Path(tmp)
        patch.chdir(tmp_path)

        repo_path = fafo._scaffold_repo(template=None, repo_name=repo_name, trust=True)

        assert repo_path == Path(repo_name)
        assert (tmp_path / repo_name).is_dir()
