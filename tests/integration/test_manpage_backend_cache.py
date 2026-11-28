"""Test preparation of uv's build-backend cache for offline builds.

The warm-up must probe offline, retry online only for a recognised cache miss,
keep other failures' diagnostics, and serialise pytest-xdist workers. These
tests exercise each decision, the process boundary, the probe's build-system
contract, and the lock invariant across worker counts and arrival orders
without invoking uv for real.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import tempfile
import threading
import time
import tomllib
import typing as typ
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.integration import manpage_backend_cache
from tests.integration.manpage_backend_cache import (
    CACHE_MISS_MARKERS,
    LOCK_EX,
    BuildCacheError,
    CacheWarmUpTools,
    CommandResult,
    build_requirements,
    environment,
    reports_cache_miss,
    run_measured_build,
    run_uv,
    warm_backend_cache,
    write_backend_probe,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_ROOT = Path(__file__).resolve().parents[2]
_CACHE_MISS_MARKER = CACHE_MISS_MARKERS[0]
_CONCURRENT_WORKERS = 2
# Long enough for an unserialised second worker to enter its probe, short
# enough to keep the concurrency tests fast.
_PROBE_WINDOW = 0.3
_MINIMAL_PYPROJECT = '[build-system]\nrequires = ["hatchling"]\n'


def _write_pyproject(directory: Path) -> Path:
    """Write a minimal pyproject.toml for the probe to read."""
    pyproject = directory / "pyproject.toml"
    pyproject.write_text(_MINIMAL_PYPROJECT, encoding="utf-8")
    return pyproject


@dataclasses.dataclass(slots=True)
class _FakeLocal:
    """Stand in for plumbum's local command factory at the process boundary."""

    calls: list[tuple[tuple[str, ...], dict[str, object]]] = dataclasses.field(
        default_factory=list
    )

    def __getitem__(self, name: str) -> _FakeLocal:
        """Return this stand-in for any requested executable."""
        return self

    def run(
        self,
        arguments: tuple[str, ...],
        **kwargs: object,
    ) -> tuple[int, str, str]:
        """Record the command and report success."""
        self.calls.append((arguments, kwargs))
        return (0, "out", "err")


def _patch_process_boundary(
    monkeypatch: pytest.MonkeyPatch,
    fake: _FakeLocal,
) -> None:
    """Point uv discovery and process execution at the stand-in."""
    monkeypatch.setattr(
        manpage_backend_cache.shutil,
        "which",
        lambda name: "/usr/bin/uv",
    )
    monkeypatch.setattr(manpage_backend_cache, "local", fake)


def test_run_uv_prepends_offline_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default command keeps the network disabled."""
    fake = _FakeLocal()
    _patch_process_boundary(monkeypatch, fake)
    env = environment(tmp_path, tmp_path / "cache")
    result = run_uv(("build", "--wheel"), tmp_path, env)
    assert result.returncode == 0, "the fake uv must succeed"
    arguments = fake.calls[0][0]
    assert arguments == ("--offline", "build", "--wheel"), (
        "uv must default to the offline prefix"
    )
    assert fake.calls[0][1]["env"] is env, "uv must receive the caller's environment"


def test_run_uv_omits_offline_when_warming(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The online warm-up is the only call without the offline prefix."""
    fake = _FakeLocal()
    _patch_process_boundary(monkeypatch, fake)
    run_uv(("build", "--wheel"), tmp_path, {}, offline=False)
    assert fake.calls[0][0] == ("build", "--wheel"), (
        "an online warm-up must not pass --offline"
    )


def test_run_uv_reports_a_missing_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing uv executable fails with the cache precondition message."""
    monkeypatch.setattr(manpage_backend_cache.shutil, "which", lambda name: None)
    with pytest.raises(BuildCacheError, match="uv is required"):
        run_uv(("build",), tmp_path, {})


def test_measured_builds_pass_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A measured packaging command must stay offline."""
    fake = _FakeLocal()
    _patch_process_boundary(monkeypatch, fake)
    env = environment(tmp_path, tmp_path / "cache")
    result = run_measured_build(("build", "--wheel"), tmp_path, env)
    assert result.returncode == 0, "the fake uv must succeed"
    assert fake.calls[0][0][0] == "--offline", "measured builds must stay offline"


def test_environment_points_uv_at_the_shared_cache(tmp_path: Path) -> None:
    """The environment names exactly one cache directory."""
    cache = tmp_path / "cache"
    env = environment(tmp_path, cache)
    assert env["UV_CACHE_DIR"] == str(cache), (
        "UV_CACHE_DIR must be the supplied cache directory"
    )


def test_warm_up_uses_the_environment_cache(tmp_path: Path) -> None:
    """The lock and uv see the single cache named by the environment."""
    cache = tmp_path / "cache"
    env = environment(tmp_path, cache)
    seen: list[dict[str, str]] = []

    def run(
        arguments: tuple[str, ...],
        cwd: Path,
        run_environment: dict[str, str],
        *,
        offline: bool = True,
    ) -> CommandResult:
        """Record the environment uv receives."""
        seen.append(run_environment)
        return CommandResult(0, "", "")

    warm_backend_cache(
        tmp_path / "work",
        env,
        _write_pyproject(tmp_path),
        tools=CacheWarmUpTools(run_uv=run),
    )
    assert seen, "the warm-up must probe the cache"
    assert all(item["UV_CACHE_DIR"] == str(cache) for item in seen), (
        "every uv call must use the environment's cache directory"
    )
    assert (cache / "manpage-build-backends.lock").exists(), (
        "the lock file belongs in the same cache directory"
    )


def test_warm_up_requires_a_cache_environment(tmp_path: Path) -> None:
    """An environment without UV_CACHE_DIR is rejected explicitly."""
    with pytest.raises(BuildCacheError, match="must set UV_CACHE_DIR"):
        warm_backend_cache(tmp_path / "work", {}, _write_pyproject(tmp_path))


def test_build_requirements_reports_an_unreadable_pyproject(tmp_path: Path) -> None:
    """A missing pyproject keeps its own diagnostic."""
    with pytest.raises(BuildCacheError, match="cannot read build-system requirements"):
        build_requirements(tmp_path / "missing.toml")


def test_write_backend_probe_declares_the_build_system(tmp_path: Path) -> None:
    """The probe resolves exactly the real build-system requirements."""
    requirements = build_requirements(_ROOT / "pyproject.toml")
    probe = write_backend_probe(tmp_path, requirements)
    actual = tomllib.loads((probe / "pyproject.toml").read_text(encoding="utf-8"))
    expected = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert actual["build-system"] == expected["build-system"], (
        "the probe must mirror the real build-system table"
    )


def test_write_backend_probe_can_be_repeated(tmp_path: Path) -> None:
    """A repeated probe write reuses the same project."""
    requirements = build_requirements(_ROOT / "pyproject.toml")
    first = write_backend_probe(tmp_path, requirements)
    second = write_backend_probe(tmp_path, requirements)
    assert first == second, "repeated writes must reuse the probe project"


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
) -> None:
    """A cache that resolves the backends needs no network access."""
    calls: list[bool] = []
    warm_backend_cache(
        tmp_path / "work",
        {"UV_CACHE_DIR": str(tmp_path / "cache")},
        _write_pyproject(tmp_path),
        tools=CacheWarmUpTools(
            run_uv=_recording_uv([CommandResult(0, "", "")], calls),
        ),
    )
    assert calls == [True], "a warm cache must only run the offline probe"


def test_backend_probe_warms_a_cold_cache_with_one_online_build(
    tmp_path: Path,
) -> None:
    """A cache miss warms online once and then resolves offline."""
    calls: list[bool] = []
    results = [
        CommandResult(2, "", _CACHE_MISS_MARKER),
        CommandResult(0, "", ""),
        CommandResult(0, "", ""),
    ]
    warm_backend_cache(
        tmp_path / "work",
        {"UV_CACHE_DIR": str(tmp_path / "cache")},
        _write_pyproject(tmp_path),
        tools=CacheWarmUpTools(run_uv=_recording_uv(results, calls)),
    )
    assert calls == [True, False, True], (
        "a cold cache must probe, warm once, then re-probe offline"
    )


def test_backend_probe_reports_a_failed_warm_up(tmp_path: Path) -> None:
    """A warm-up failure keeps the cache precondition visible."""
    calls: list[bool] = []
    results = [
        CommandResult(2, "", _CACHE_MISS_MARKER),
        CommandResult(1, "", "network unreachable"),
    ]
    with pytest.raises(BuildCacheError, match="warming them failed"):
        warm_backend_cache(
            tmp_path / "work",
            {"UV_CACHE_DIR": str(tmp_path / "cache")},
            _write_pyproject(tmp_path),
            tools=CacheWarmUpTools(run_uv=_recording_uv(results, calls)),
        )
    assert calls == [True, False], (
        "a failed warm-up must stop before the offline re-probe"
    )


def test_backend_probe_preserves_a_non_cache_failure(tmp_path: Path) -> None:
    """A probe failure unrelated to the cache is not retried online."""
    calls: list[bool] = []
    results = [CommandResult(2, "", "No space left on device")]
    with pytest.raises(
        BuildCacheError,
        match="reason other than a missing cache entry",
    ) as caught:
        warm_backend_cache(
            tmp_path / "work",
            {"UV_CACHE_DIR": str(tmp_path / "cache")},
            _write_pyproject(tmp_path),
            tools=CacheWarmUpTools(run_uv=_recording_uv(results, calls)),
        )
    assert "No space left on device" in str(caught.value), (
        "a non-cache probe failure must keep its original diagnostic"
    )
    assert calls == [True], "only the offline probe may run for a build failure"


@pytest.mark.parametrize("marker", CACHE_MISS_MARKERS)
@pytest.mark.parametrize(("returncode", "expected"), [(0, False), (2, True)])
def test_cache_miss_detection_requires_a_failing_status(
    marker: str,
    returncode: int,
    *,
    expected: bool,
) -> None:
    """A successful result is never a cache miss, even with a marker present."""
    result = CommandResult(returncode, "", f"prefix {marker} suffix")
    assert reports_cache_miss(result) is expected, (
        "classification must require a failing status and a recognised marker"
    )


def test_cache_miss_detection_rejects_other_failures() -> None:
    """A failure without an offline marker keeps its own diagnostic."""
    assert not reports_cache_miss(CommandResult(2, "", "No space left on device")), (
        "a non-cache failure must not be treated as a cache miss"
    )


@given(
    returncode=st.integers(min_value=-255, max_value=255),
    output=st.one_of(
        st.text(),
        st.sampled_from(CACHE_MISS_MARKERS),
        st.tuples(
            st.text(),
            st.sampled_from(CACHE_MISS_MARKERS),
            st.text(),
        ).map("".join),
    ),
)
def test_cache_miss_detection_requires_status_and_marker(
    returncode: int,
    output: str,
) -> None:
    """Only a failing probe carrying a recognised marker is a cache miss."""
    expected = returncode != 0 and any(
        marker in output for marker in CACHE_MISS_MARKERS
    )
    assert reports_cache_miss(CommandResult(returncode, "", output)) is expected, (
        "classification must require a failing status and a recognised marker"
    )


@settings(deadline=None)
@given(
    probe=st.sampled_from(["hit", "miss", "error"]),
    warm=st.sampled_from(["ok", "fail"]),
    reprobe=st.sampled_from(["ok", "fail"]),
)
def test_warm_up_outcome_sequences_follow_the_cache_policy(
    probe: str,
    warm: str,
    reprobe: str,
) -> None:
    """Every probe, warm-up and re-probe outcome follows the cache policy."""
    calls: list[bool] = []
    results: list[CommandResult] = []
    expected_calls: list[bool]
    expected_error: str | None
    match probe:
        case "hit":
            results = [CommandResult(0, "", "")]
            expected_calls = [True]
            expected_error = None
        case "error":
            results = [CommandResult(2, "", "No space left on device")]
            expected_calls = [True]
            expected_error = "reason other than a missing cache entry"
        case _:
            results = [CommandResult(2, "", CACHE_MISS_MARKERS[0])]
            match warm:
                case "fail":
                    results.append(CommandResult(1, "", "network unreachable"))
                    expected_calls = [True, False]
                    expected_error = "warming them failed"
                case _:
                    results.append(CommandResult(0, "", ""))
                    results.append(
                        CommandResult(0, "", "")
                        if reprobe == "ok"
                        else CommandResult(1, "", "cache race")
                    )
                    expected_calls = [True, False, True]
                    expected_error = (
                        None if reprobe == "ok" else "warmed cache must resolve"
                    )
    tools = CacheWarmUpTools(run_uv=_recording_uv(results, calls))
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        env = {"UV_CACHE_DIR": str(root / "cache")}
        pyproject = _write_pyproject(root)
        if expected_error is None:
            warm_backend_cache(root / "work", env, pyproject, tools=tools)
        else:
            with pytest.raises(BuildCacheError, match=expected_error):
                warm_backend_cache(root / "work", env, pyproject, tools=tools)
    assert calls == expected_calls, "the warm-up must follow the cache policy"


@dataclasses.dataclass(slots=True)
class _CacheModel:
    """Model a cold uv cache and the exclusive lock around its warm-up."""

    workers: int = _CONCURRENT_WORKERS
    delay: float = _PROBE_WINDOW
    calls: list[bool] = dataclasses.field(default_factory=list)
    cached: bool = False
    online: int = 0
    probes: int = 0
    offline_misses: int = 0
    online_without_miss: int = 0
    probes_while_held: int = 0
    lock_attempts: int = 0
    missed: dict[int, bool] = dataclasses.field(default_factory=dict)
    owner: int | None = None
    state: threading.Lock = dataclasses.field(default_factory=threading.Lock)
    first_probe: threading.Event = dataclasses.field(default_factory=threading.Event)
    all_attempted: threading.Event = dataclasses.field(default_factory=threading.Event)

    def _record_probe(self) -> tuple[bool, bool]:
        """Record one offline probe and return (first cold probe, missed)."""
        ident = threading.get_ident()
        with self.state:
            self.calls.append(True)
            self.probes += 1
            if self.owner not in {None, ident}:
                self.probes_while_held += 1
            self.missed[ident] = not self.cached
            if self.cached:
                return False, False
            self.offline_misses += 1
            first = not self.first_probe.is_set()
            self.first_probe.set()
            return first, True

    def run(
        self,
        arguments: tuple[str, ...],
        cwd: Path,
        environment: dict[str, str],
        *,
        offline: bool = True,
    ) -> CommandResult:
        """Model a cache that fills after the first online build."""
        if offline:
            first, missed = self._record_probe()
            if not missed:
                return CommandResult(0, "", "")
            if first:
                # Hold the first cold probe open until every worker reaches the
                # cache lock. Serialised workers block there; unserialised ones
                # probe during this window, which the property detects.
                assert self.all_attempted.wait(timeout=10), (
                    "every worker must attempt the cache lock"
                )
            time.sleep(self.delay)
            return CommandResult(2, "", _CACHE_MISS_MARKER)
        ident = threading.get_ident()
        with self.state:
            self.calls.append(False)
            if not self.missed.get(ident, False):
                self.online_without_miss += 1
            self.cached = True
            self.online += 1
        return CommandResult(0, "", "")

    def track_flock(self, descriptor: int | typ.IO[str], operation: int) -> None:
        """Record lock ownership and attempts before delegating to flock."""
        if operation == LOCK_EX:
            with self.state:
                self.lock_attempts += 1
                if self.lock_attempts == self.workers:
                    self.all_attempted.set()
            manpage_backend_cache.flock(descriptor, operation)
            with self.state:
                self.owner = threading.get_ident()
            return
        with self.state:
            self.owner = None
        manpage_backend_cache.flock(descriptor, operation)


def test_backend_probe_warms_once_for_concurrent_workers(tmp_path: Path) -> None:
    """Workers sharing a cold cache perform one online warm-up."""
    model = _CacheModel()
    env = {"UV_CACHE_DIR": str(tmp_path / "cache")}
    pyproject = _write_pyproject(tmp_path)
    tools = CacheWarmUpTools(run_uv=model.run, file_lock=model.track_flock)

    def warm(index: int) -> None:
        """Warm the shared cache from one worker."""
        warm_backend_cache(tmp_path / f"work-{index}", env, pyproject, tools=tools)

    with concurrent.futures.ThreadPoolExecutor(max_workers=_CONCURRENT_WORKERS) as pool:
        futures = [pool.submit(warm, index) for index in range(_CONCURRENT_WORKERS)]
        for future in futures:
            future.result(timeout=60)
    assert model.offline_misses == 1, "only one worker may miss the cold cache"
    assert model.online == 1, "concurrent workers must warm the cache once"
    assert model.calls.count(False) == 1, "only the warm-up may run online"
    assert model.probes_while_held == 0, "no probe may run while the lock is held"


@settings(deadline=None, max_examples=10)
@given(
    start_delays_ms=st.lists(
        st.integers(min_value=0, max_value=20),
        min_size=2,
        max_size=5,
    ),
    delay_ms=st.integers(min_value=0, max_value=50),
)
def test_warm_up_lock_invariant_holds_for_any_arrival_order(
    tmp_path_factory: pytest.TempPathFactory,
    start_delays_ms: list[int],
    delay_ms: int,
) -> None:
    """Any worker count and arrival order warms once under the lock."""
    workers = len(start_delays_ms)
    model = _CacheModel(workers=workers, delay=delay_ms / 1000)
    root = tmp_path_factory.mktemp("warm-up-property")
    env = {"UV_CACHE_DIR": str(root / "cache")}
    pyproject = _write_pyproject(root)
    tools = CacheWarmUpTools(run_uv=model.run, file_lock=model.track_flock)

    def warm(index: int) -> None:
        """Start this worker after its generated delay."""
        time.sleep(start_delays_ms[index] / 1000)
        warm_backend_cache(root / f"work-{index}", env, pyproject, tools=tools)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(warm, index) for index in range(workers)]
        for future in futures:
            future.result(timeout=120)
    assert model.offline_misses == 1, "one cold probe must serve every worker"
    assert model.online == 1, "a cold cache must warm exactly once"
    assert model.calls.count(False) == 1, "only the warm-up may run online"
    assert model.probes_while_held == 0, (
        "no probe may run while another worker holds the lock"
    )
    assert model.online_without_miss == 0, "an online build must follow a cache miss"
