"""Exercise manpage generation, wheel installation, and sdist rebuilding."""

from __future__ import annotations

import base64
import csv
import dataclasses
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import tomllib
import typing as typ
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

import pytest
from plumbum import local

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from email.message import Message

if sys.platform == "win32":
    pytest.skip(
        "the packaging tests build POSIX virtual environments",
        allow_module_level=True,
    )
else:
    import fcntl

_ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.timeout(180)


@dataclasses.dataclass(frozen=True, slots=True)
class DistributionBuild:
    """Keep built distributions and their isolated test environment together.

    Attributes
    ----------
    root : Path
        Temporary build workspace.
    wheel : Path
        Wheel built directly from the source tree.
    sdist : Path
        Source distribution containing the manual sources.
    rebuilt_wheel : Path
        Wheel rebuilt independently from the source distribution.
    environment : dict[str, str]
        Environment with isolated user data and tool directories.

    """

    root: Path
    wheel: Path
    sdist: Path
    rebuilt_wheel: Path
    environment: dict[str, str]


def _commands() -> tuple[str, ...]:
    """Read the installed command names rather than duplicate their inventory."""
    config = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return tuple(config["project"]["scripts"])


def _copy_sources(destination: Path) -> Path:
    """Copy only build inputs, without Git metadata or pre-generated pages."""
    destination.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(_ROOT / name, destination / name)
    for name in ("git_donkey", "docs/man"):
        shutil.copytree(
            _ROOT / name,
            destination / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.1"),
        )
    return destination


def _environment(root: Path) -> dict[str, str]:
    """Isolate user-facing paths and reuse the build cache the fixture warms."""
    # Makefile UV_ENV overrides the setup-uv action's outer cache setting.
    cache = _ROOT / ".uv-cache"
    return os.environ | {
        "UV_CACHE_DIR": str(cache),
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_LINK_MODE": "copy",
        "XDG_DATA_HOME": str(root / "xdg-data"),
        "XDG_CONFIG_HOME": str(root / "xdg-config"),
        "DOCUTILSCONFIG": "",
    }


def _venv_python(environment: Path) -> Path:
    """Return the interpreter inside an isolated installation environment."""
    return environment / "bin/python"


@dataclasses.dataclass(frozen=True, slots=True)
class CommandResult:
    """Capture an external command's exit status and output.

    Attributes
    ----------
    returncode : int
        Exit status reported by the process.
    stdout : str
        Captured standard output.
    stderr : str
        Captured standard error.

    """

    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        """Combined output, so diagnostics survive a failed assertion."""
        return self.stdout + self.stderr


def _run_uv(
    arguments: tuple[str, ...],
    cwd: Path,
    environment: dict[str, str],
    *,
    offline: bool = True,
) -> CommandResult:
    """Run uv without shell parsing, offline unless a caller is warming the cache."""
    executable = shutil.which("uv")
    assert executable is not None, "uv is required for packaging integration tests"
    prefix = ("--offline",) if offline else ()
    returncode, stdout, stderr = local[executable].run(
        (*prefix, *arguments),
        retcode=None,
        cwd=cwd,
        env=environment,
        timeout=120,
    )
    return CommandResult(returncode, stdout, stderr)


def _assert_success(result: CommandResult) -> None:
    """Include build diagnostics in a failed test rather than hide stderr."""
    assert result.returncode == 0, result.output


def _manuals(wheel: Path) -> dict[str, bytes]:
    """Read every manpage from its installed-data location in a wheel."""
    with zipfile.ZipFile(wheel) as archive:
        return {
            PurePosixPath(name).name: archive.read(name)
            for name in archive.namelist()
            if ".data/data/share/man/man1/" in name
        }


def _package_metadata(wheel: Path) -> Message:
    """Read the core metadata embedded in a built wheel."""
    with zipfile.ZipFile(wheel) as archive:
        metadata_paths = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        assert len(metadata_paths) == 1, (
            f"wheel must contain one METADATA file: {metadata_paths}"
        )
        return BytesParser(policy=policy.default).parsebytes(
            archive.read(metadata_paths[0])
        )


def _build_requirements() -> tuple[str, ...]:
    """Read the build backends the packaging builds must resolve."""
    config = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return tuple(config["build-system"]["requires"])


def _build_backend_probe(root: Path) -> Path:
    """Write a minimal project that resolves only the build-system requirements.

    Keeping the probe free of the manual-generation hook means a failed probe
    reports a cache miss rather than a defect in the real build.

    Returns
    -------
    Path
        Root of the generated probe project.

    """
    project = root / "probe"
    package = project / "src" / "build_backend_probe"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    requires = ", ".join(json.dumps(item) for item in _build_requirements())
    (project / "pyproject.toml").write_text(
        "\n".join((
            "[build-system]",
            f"requires = [{requires}]",
            'build-backend = "hatchling.build"',
            "",
            "[project]",
            'name = "build-backend-probe"',
            'version = "0.0.0"',
            "",
            "[tool.hatch.build.targets.wheel]",
            'packages = ["src/build_backend_probe"]',
            "",
        )),
        encoding="utf-8",
    )
    return project


def _warm_backend_cache(
    cache: Path,
    work: Path,
    environment: dict[str, str],
) -> None:
    """Prove the build backends resolve offline, warming a cold cache once.

    The lock serialises pytest-xdist workers, which each run a session fixture,
    so a cold checkout still performs a single online warm-up. The probe and
    the measured builds stay offline once the backends are cached.
    """
    cache.mkdir(parents=True, exist_ok=True)
    probe = _build_backend_probe(work)
    run_environment = environment | {"UV_CACHE_DIR": str(cache)}
    build = ("build", "--python", sys.executable, "--wheel")
    lock_path = cache / "manpage-build-backends.lock"
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if _run_uv(build, probe, run_environment).returncode == 0:
                return
            warmed = _run_uv(build, probe, run_environment, offline=False)
            assert warmed.returncode == 0, (
                "the build backends must be cached before the offline builds "
                f"run; warming them failed:\n{warmed.output}"
            )
            cached = _run_uv(build, probe, run_environment)
            assert cached.returncode == 0, (
                "the warmed cache must resolve the build backends offline:\n"
                f"{cached.output}"
            )
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@pytest.fixture(scope="session")
def build_backends(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Make the build backends resolvable by the module's offline builds.

    A fresh checkout starts with an empty ``.uv-cache``, so its offline builds
    cannot resolve ``hatchling``, ``hatch-build-scripts`` or ``docutils`` and
    every test in this module fails for a reason unrelated to the code under
    test. Prove the cache satisfies them before the measured builds run, and
    warm it with one online build only when it cannot. The measured builds stay
    offline, so they still prove that building a distribution needs no network.
    """
    root = tmp_path_factory.mktemp("manpage-backends")
    _warm_backend_cache(_ROOT / ".uv-cache", root, _environment(root))


@pytest.fixture(scope="module")
def distributions(
    tmp_path_factory: pytest.TempPathFactory,
    build_backends: None,
) -> DistributionBuild:
    """Build from checkout inputs and then independently from the sdist.

    Returns
    -------
    DistributionBuild
        Both wheels, the sdist, and an isolated installation environment.

    """
    root = tmp_path_factory.mktemp("manpage-packaging")
    source = _copy_sources(root / "source")
    environment = _environment(root)
    # Stale outputs must never hide a broken or omitted generation command.
    for command in _commands():
        (source / f"docs/man/{command}.1").write_text("stale manual", encoding="utf-8")
    _assert_success(
        _run_uv(
            ("build", "--python", sys.executable, "--sdist", "--wheel"),
            source,
            environment,
        )
    )
    (wheel,) = (source / "dist").glob("*.whl")
    (sdist,) = (source / "dist").glob("*.tar.gz")
    _assert_success(
        _run_uv(
            (
                "build",
                "--python",
                sys.executable,
                "--wheel",
                str(sdist),
                "--out-dir",
                str(root / "rebuilt"),
            ),
            root,
            environment,
        )
    )
    (rebuilt_wheel,) = (root / "rebuilt").glob("*.whl")
    return DistributionBuild(root, wheel, sdist, rebuilt_wheel, environment)


def test_wheel_contains_complete_recorded_manpages(
    distributions: DistributionBuild,
) -> None:
    """Ship real roff pages, with hashes in RECORD and no misplaced copies."""
    manuals = _manuals(distributions.wheel)
    assert set(manuals) == {f"{command}.1" for command in _commands()}, (
        f"wheel must ship one page per installed command: {sorted(manuals)}"
    )
    for name, content in manuals.items():
        assert b".TH " in content, f"{name} must open with a roff title"
        assert b".SH SYNOPSIS" in content, f"{name} must carry a synopsis section"
        assert b".SH DESCRIPTION" in content, f"{name} must carry a description"
        assert b"stale manual" not in content, f"generation left {name} stale"
        assert b"\x1b" not in content, f"{name} must not carry terminal escapes"
    with zipfile.ZipFile(distributions.wheel) as archive:
        (record,) = (name for name in archive.namelist() if name.endswith("/RECORD"))
        records = {
            row[0]: row[1:]
            for row in csv.reader(io.StringIO(archive.read(record).decode()))
        }
        pages = [name for name in archive.namelist() if name.endswith(".1")]
        assert len(pages) == len(manuals), "wheel must carry one copy of each page"
        for name in pages:
            assert ".data/data/share/man/man1/" in name, (
                f"{name} is outside the wheel data scheme"
            )
            content = archive.read(name)
            digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest())
            assert records[name] == [
                "sha256=" + digest.rstrip(b"=").decode(),
                str(len(content)),
            ], f"RECORD must hash the installed bytes of {name}"


def test_wheels_publish_project_metadata(distributions: DistributionBuild) -> None:
    """Publish complete PyPI metadata from source and rebuilt distributions."""
    expected_project_urls = [
        "Homepage, https://df12.studio",
        "Repository, https://github.com/leynos/git-donkey",
        "Issues, https://github.com/leynos/git-donkey/issues",
    ]
    expected_classifiers = [
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: ISC License (ISCL)",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.13",
        "Topic :: Software Development :: Version Control :: Git",
    ]
    expected_readme_links = [
        '[pypi]: https://img.shields.io/pypi/v/git-donkey "PyPI package"',
        "[package]: https://pypi.org/project/git-donkey/",
        "https://github.com/leynos/git-donkey/blob/main/skill/git-donkey-worktrees/SKILL.md",
        "https://github.com/leynos/git-donkey/blob/main/skill/git-donkey-worktrees/references/cleanup.md",
        "https://github.com/leynos/git-donkey/blob/main/docs/users-guide.md",
        "https://github.com/leynos/git-donkey/blob/main/LICENSE",
        "https://github.com/leynos/git-donkey/blob/main/AGENTS.md",
    ]

    for wheel in (distributions.wheel, distributions.rebuilt_wheel):
        metadata = _package_metadata(wheel)
        assert metadata["Summary"] == (
            "Git subcommands for branch-based development with linked worktrees"
        ), f"{wheel.name} should publish the package summary"
        assert metadata["Keywords"] == "cli,developer-tools,git,worktree", (
            f"{wheel.name} should publish the search keywords"
        )
        assert metadata.get_all("Project-URL") == expected_project_urls, (
            f"{wheel.name} should publish the project URLs"
        )
        assert metadata.get_all("Classifier") == expected_classifiers, (
            f"{wheel.name} should publish the classifiers"
        )
        readme = metadata.get_payload()
        assert isinstance(readme, str), f"{wheel.name} README should be text"
        for expected_link in expected_readme_links:
            assert expected_link in readme, (
                f"{wheel.name} README should publish {expected_link}"
            )


def test_sdist_contains_sources_not_generated_pages(
    distributions: DistributionBuild,
) -> None:
    """Require a self-contained sdist without stale generated manpages."""
    with tarfile.open(distributions.sdist) as archive:
        paths = {
            PurePosixPath(*PurePosixPath(name).parts[1:]).as_posix()
            for name in archive.getnames()
        }
    assert "docs/man/docutils.conf" in paths, "sdist must carry the generator config"
    assert {f"docs/man/{command}.rst" for command in _commands()} <= paths, (
        "sdist must carry every manual source"
    )
    assert not any(path.endswith(".1") for path in paths), (
        "sdist must not ship generated pages"
    )
    assert _manuals(distributions.rebuilt_wheel) == _manuals(distributions.wheel), (
        "rebuilding from the sdist must reproduce the pages byte for byte"
    )


def test_install_places_and_removes_environment_manpages(
    distributions: DistributionBuild,
) -> None:
    """Install without promoting files into the user manpath."""
    environment = distributions.root / "environment"
    _assert_success(
        _run_uv(
            ("venv", "--python", sys.executable, str(environment)),
            distributions.root,
            distributions.environment,
        )
    )
    interpreter = _venv_python(environment)
    # Page placement does not depend on the runtime dependency graph, and that
    # graph cannot be resolved offline: loctocat requires halo, whose only
    # published wheel targets Python 2.
    _assert_success(
        _run_uv(
            (
                "pip",
                "install",
                "--no-deps",
                "--python",
                str(interpreter),
                str(distributions.wheel),
            ),
            distributions.root,
            distributions.environment,
        )
    )
    installed = environment / "share/man/man1"
    assert {path.name: path.read_bytes() for path in installed.glob("*.1")} == _manuals(
        distributions.wheel
    ), "installed pages must match the wheel contents"
    assert not (distributions.root / "xdg-data/man").exists(), (
        "installing must not populate the user manpath"
    )
    _assert_success(
        _run_uv(
            ("pip", "uninstall", "--python", str(interpreter), "git-donkey"),
            distributions.root,
            distributions.environment,
        )
    )
    assert not list(installed.glob("*.1")), "uninstall must remove the pages"
    assert not (distributions.root / "xdg-data/man").exists(), (
        "uninstall must leave the user manpath untouched"
    )


@pytest.mark.parametrize("failure", ["missing", "malformed"])
def test_broken_manual_source_fails_the_build(
    tmp_path: Path,
    failure: str,
    build_backends: None,
) -> None:
    """Refuse missing or malformed sources instead of shipping stale pages."""
    source = _copy_sources(tmp_path / "source")
    manual = source / "docs/man/git-donkey.rst"
    (source / "docs/man/git-donkey.1").write_text("stale manual", encoding="utf-8")
    if failure == "missing":
        manual.unlink()
    else:
        with manual.open("a", encoding="utf-8") as stream:
            stream.write("\n.. nonexistent-manpage-directive::\n")
    result = _run_uv(
        ("build", "--python", sys.executable, "--wheel"),
        source,
        _environment(tmp_path),
    )
    assert result.returncode != 0, f"a {failure} source must fail the build"
    assert "git-donkey.rst" in result.output, "build must name the offending source"
    assert not list((source / "dist").glob("*.whl")), (
        "a failed build must not publish a wheel"
    )


def test_backend_probe_declares_the_project_build_system(tmp_path: Path) -> None:
    """The probe resolves exactly the real build-system requirements."""
    probe = _build_backend_probe(tmp_path)
    actual = tomllib.loads((probe / "pyproject.toml").read_text(encoding="utf-8"))
    expected = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert actual["build-system"] == expected["build-system"], (
        "the probe must mirror the real build-system table"
    )


def _recording_uv(
    results: cabc.Iterable[CommandResult],
    calls: list[bool],
) -> cabc.Callable[..., CommandResult]:
    """Return a uv stand-in that records offline flags and yields fixed results."""
    remaining = iter(results)

    def run(
        arguments: tuple[str, ...],
        cwd: Path,
        environment: dict[str, str],
        *,
        offline: bool = True,
    ) -> CommandResult:
        """Record the offline flag and return the next fixed result."""
        calls.append(offline)
        return next(remaining)

    return run


def test_backend_probe_skips_the_online_build_when_backends_are_cached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cache that resolves the backends needs no network access."""
    calls: list[bool] = []
    monkeypatch.setattr(
        sys.modules[__name__],
        "_run_uv",
        _recording_uv([CommandResult(0, "", "")], calls),
    )
    _warm_backend_cache(tmp_path / "cache", tmp_path / "work", {})
    assert calls == [True], "a warm cache must only run the offline probe"


def test_backend_probe_warms_a_cold_cache_with_one_online_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cache miss warms online once and then resolves offline."""
    calls: list[bool] = []
    results = [
        CommandResult(2, "", "network disabled"),
        CommandResult(0, "", ""),
        CommandResult(0, "", ""),
    ]
    monkeypatch.setattr(
        sys.modules[__name__],
        "_run_uv",
        _recording_uv(results, calls),
    )
    _warm_backend_cache(tmp_path / "cache", tmp_path / "work", {})
    assert calls == [True, False, True], (
        "a cold cache must probe, warm once, then re-probe offline"
    )


def test_backend_probe_reports_a_failed_warm_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A warm-up failure keeps the cache precondition visible."""
    calls: list[bool] = []
    results = [
        CommandResult(2, "", "network disabled"),
        CommandResult(1, "", "network unreachable"),
    ]
    monkeypatch.setattr(
        sys.modules[__name__],
        "_run_uv",
        _recording_uv(results, calls),
    )
    with pytest.raises(AssertionError, match="warming them failed"):
        _warm_backend_cache(tmp_path / "cache", tmp_path / "work", {})
    assert calls == [True, False], (
        "a failed warm-up must stop before the offline re-probe"
    )
