"""Exercise manpage generation, wheel installation, and sdist rebuilding."""

from __future__ import annotations

import base64
import csv
import dataclasses
import hashlib
import io
import os
import shutil
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

import pytest
from plumbum import local

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
    """Isolate user-facing paths and reuse the build cache populated by make build."""
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
) -> CommandResult:
    """Run the required uv executable without shell parsing or network access."""
    executable = shutil.which("uv")
    assert executable is not None, "uv is required for packaging integration tests"
    returncode, stdout, stderr = local[executable].run(
        ("--offline", *arguments),
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


@pytest.fixture(scope="module")
def distributions(tmp_path_factory: pytest.TempPathFactory) -> DistributionBuild:
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
def test_broken_manual_source_fails_the_build(tmp_path: Path, failure: str) -> None:
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
