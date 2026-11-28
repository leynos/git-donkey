"""Prepare uv's build-backend cache for the manpage packaging tests.

The packaging tests build distributions with ``uv --offline`` against the
worktree-local ``.uv-cache``. A fresh checkout has an empty cache, so the build
backends cannot resolve and every packaging test fails for a reason unrelated
to the code under test. This module makes that precondition explicit: it writes
a minimal probe project declaring the repository's exact ``[build-system]``
requirements, performs one online build only when uv reports a cache miss, and
leaves every other failure with its own diagnostic.

pytest-xdist runs a session fixture once per worker, so an exclusive file lock
in the cache directory serialises the probe and warm-up. The measured packaging
builds stay offline, so they still prove a distribution build needs no network.
``docs/manpages-design.md`` records the design decisions.

The probe, classification, process wrapper and lock stay together because they
form one test-support adapter: warming this cache is the only reason each of
them exists, and the probe is only meaningful when its failure can be
classified as a cache miss. Keep the cache location in one place: the
``UV_CACHE_DIR`` in the environment passed to ``warm_backend_cache``.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import sys
import tomllib
import typing as typ
from pathlib import Path

import pytest
from plumbum import local

if typ.TYPE_CHECKING:
    import collections.abc as cabc

if sys.platform == "win32":
    pytest.skip(
        "the packaging tests build POSIX virtual environments",
        allow_module_level=True,
    )
else:
    from fcntl import LOCK_EX, LOCK_UN, flock

# uv reports an uncached requirement through either the resolver hint or the
# client's bare cache-miss message. Only a failure carrying one of them may be
# retried online; any other failure keeps its own diagnostic instead of being
# mislabelled as a cache miss.
CACHE_MISS_MARKERS = (
    "because the network was disabled",
    "Network connectivity is disabled, but the requested data wasn't found",
)


class BuildCacheError(RuntimeError):
    """The build-backend cache cannot satisfy the offline builds."""

    def __init__(self, summary: str, detail: str) -> None:
        """Keep uv's diagnostic with the failure."""
        super().__init__(f"{summary}:\n{detail}")
        self.detail = detail


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


def run_uv(
    arguments: tuple[str, ...],
    cwd: Path,
    environment: dict[str, str],
    *,
    offline: bool = True,
) -> CommandResult:
    """Run uv without shell parsing, offline unless a caller is warming the cache."""
    executable = shutil.which("uv")
    if executable is None:
        message = "uv is required for packaging integration tests"
        raise BuildCacheError(message, "uv was not found on PATH")
    prefix = ("--offline",) if offline else ()
    returncode, stdout, stderr = local[executable].run(
        (*prefix, *arguments),
        retcode=None,
        cwd=cwd,
        env=environment,
        timeout=120,
    )
    return CommandResult(returncode, stdout, stderr)


def run_measured_build(
    arguments: tuple[str, ...],
    cwd: Path,
    environment: dict[str, str],
) -> CommandResult:
    """Run one measured packaging command with the network disabled."""
    return run_uv(arguments, cwd, environment, offline=True)


def environment(root: Path, cache: Path) -> dict[str, str]:
    """Isolate user-facing paths and point uv at the shared build cache."""
    return os.environ | {
        "UV_CACHE_DIR": str(cache),
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_LINK_MODE": "copy",
        "XDG_DATA_HOME": str(root / "xdg-data"),
        "XDG_CONFIG_HOME": str(root / "xdg-config"),
        "DOCUTILSCONFIG": "",
    }


def build_requirements(pyproject: Path) -> tuple[str, ...]:
    """Read the build backends declared by a pyproject.toml."""
    try:
        config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        return tuple(config["build-system"]["requires"])
    except (OSError, KeyError, tomllib.TOMLDecodeError) as error:
        message = f"cannot read build-system requirements from {pyproject}"
        raise BuildCacheError(message, str(error)) from error


def write_backend_probe(root: Path, requirements: cabc.Sequence[str]) -> Path:
    """Write a minimal project that resolves only the given build requirements.

    Keeping the probe free of the manual-generation hook isolates build-backend
    resolution from the real build, so a failure points at the cache or the
    build environment rather than at the manual sources. Repeated calls reuse
    the directory and overwrite the generated files.

    Returns
    -------
    Path
        Root of the generated probe project.

    """
    project = root / "probe"
    package = project / "src" / "build_backend_probe"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    requires = ", ".join(json.dumps(item) for item in requirements)
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


def reports_cache_miss(result: CommandResult) -> bool:
    """Whether an offline uv failure reports a requirement missing from the cache."""
    return result.returncode != 0 and any(
        marker in result.output for marker in CACHE_MISS_MARKERS
    )


type RunUv = typ.Callable[..., CommandResult]
type FileLock = typ.Callable[[int | typ.IO[str], int], None]


@dataclasses.dataclass(frozen=True, slots=True)
class CacheWarmUpTools:
    """Injected dependencies for warming the build-backend cache."""

    run_uv: RunUv = run_uv
    file_lock: FileLock = flock


CACHE_WARM_UP_TOOLS = CacheWarmUpTools()


def warm_backend_cache(
    work: Path,
    environment: dict[str, str],
    pyproject: Path,
    *,
    tools: CacheWarmUpTools = CACHE_WARM_UP_TOOLS,
) -> None:
    """Prove the build backends resolve offline, warming a cold cache once.

    The lock serialises pytest-xdist workers, which each run a session fixture,
    so a cold checkout still performs a single online warm-up. Only a reported
    cache miss permits that online build; any other probe failure keeps its own
    diagnostic. The probe and measured builds stay offline once cached. The
    environment's ``UV_CACHE_DIR`` is the single source of the cache location.

    Raises
    ------
    BuildCacheError
        If the environment omits ``UV_CACHE_DIR``, the pyproject cannot be
        read, the probe fails for a non-cache reason, the warm-up fails, or the
        warmed cache still cannot resolve the build backends offline.

    """
    try:
        cache = Path(environment["UV_CACHE_DIR"])
    except KeyError as error:
        message = "the warm-up environment must set UV_CACHE_DIR"
        raise BuildCacheError(message, str(error)) from error
    probe = write_backend_probe(work, build_requirements(pyproject))
    build = ("build", "--python", sys.executable, "--wheel")
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / "manpage-build-backends.lock").open("a", encoding="utf-8") as lock:
        tools.file_lock(lock, LOCK_EX)
        try:
            failed = tools.run_uv(build, probe, environment)
            if failed.returncode == 0:
                return
            if not reports_cache_miss(failed):
                message = (
                    "the build-backend probe failed for a reason other than a "
                    "missing cache entry"
                )
                raise BuildCacheError(message, failed.output)
            warmed = tools.run_uv(build, probe, environment, offline=False)
            if warmed.returncode != 0:
                message = (
                    "the build backends must be cached before the offline "
                    "builds run; warming them failed"
                )
                raise BuildCacheError(message, warmed.output)
            cached = tools.run_uv(build, probe, environment)
            if cached.returncode != 0:
                message = "the warmed cache must resolve the build backends offline"
                raise BuildCacheError(message, cached.output)
        finally:
            tools.file_lock(lock, LOCK_UN)
