"""Test preparation of uv's build-backend cache for offline builds.

The warm-up must probe offline, retry online only for a recognised cache miss,
keep other failures' diagnostics, and serialise pytest-xdist workers. These
tests exercise each decision, the probe's build-system contract, and the lock
invariant across worker counts without invoking uv itself.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import threading
import time
import tomllib
import typing as typ
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.integration.manpage_backend_cache import (
    CACHE_MISS_MARKERS,
    LOCK_EX,
    BuildCacheError,
    CacheWarmUpTools,
    CommandResult,
    build_backend_probe,
    flock,
    reports_cache_miss,
    warm_backend_cache,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_ROOT = Path(__file__).resolve().parents[2]
_CACHE_MISS_MARKER = CACHE_MISS_MARKERS[0]
_CONCURRENT_WORKERS = 2
# Long enough for an unserialised second worker to enter its probe, short
# enough to keep the concurrency tests fast.
_PROBE_WINDOW = 0.3


def test_backend_probe_declares_the_project_build_system(tmp_path: Path) -> None:
    """The probe resolves exactly the real build-system requirements."""
    probe = build_backend_probe(tmp_path)
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
) -> None:
    """A cache that resolves the backends needs no network access."""
    calls: list[bool] = []
    warm_backend_cache(
        tmp_path / "cache",
        tmp_path / "work",
        {},
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
        tmp_path / "cache",
        tmp_path / "work",
        {},
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
            tmp_path / "cache",
            tmp_path / "work",
            {},
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
            tmp_path / "cache",
            tmp_path / "work",
            {},
            tools=CacheWarmUpTools(run_uv=_recording_uv(results, calls)),
        )
    assert "No space left on device" in str(caught.value), (
        "a non-cache probe failure must keep its original diagnostic"
    )
    assert calls == [True], "only the offline probe may run for a build failure"


@pytest.mark.parametrize("message", CACHE_MISS_MARKERS)
def test_cache_miss_detection_accepts_both_uv_offline_messages(message: str) -> None:
    """Both uv offline messages permit the online warm-up."""
    assert reports_cache_miss(CommandResult(2, "", message)), (
        f"uv's offline cache-miss form must be recognised: {message}"
    )


def test_cache_miss_detection_rejects_other_failures() -> None:
    """A failure without an offline marker keeps its own diagnostic."""
    assert not reports_cache_miss(CommandResult(2, "", "No space left on device")), (
        "a non-cache failure must not be treated as a cache miss"
    )


@given(
    returncode=st.integers(min_value=-255, max_value=255),
    output=st.text(),
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
    miss_seen: bool = False
    owner: int | None = None
    state: threading.Lock = dataclasses.field(default_factory=threading.Lock)
    first_probe: threading.Event = dataclasses.field(default_factory=threading.Event)
    all_attempted: threading.Event = dataclasses.field(default_factory=threading.Event)

    def _record_probe(self) -> tuple[bool, bool]:
        """Record one offline probe and return (first cold probe, missed)."""
        with self.state:
            self.calls.append(True)
            self.probes += 1
            if self.owner not in {None, threading.get_ident()}:
                self.probes_while_held += 1
            if self.cached:
                return False, False
            self.offline_misses += 1
            self.miss_seen = True
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
        with self.state:
            self.calls.append(False)
            if not self.miss_seen:
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
            flock(descriptor, operation)
            with self.state:
                self.owner = threading.get_ident()
            return
        with self.state:
            self.owner = None
        flock(descriptor, operation)


def _submit_warm_ups(
    pool: concurrent.futures.ThreadPoolExecutor,
    model: _CacheModel,
    root: Path,
    workers: int,
) -> list[concurrent.futures.Future[None]]:
    """Submit one warm-up per worker against the shared cache."""
    cache = root / "cache"
    tools = CacheWarmUpTools(run_uv=model.run, file_lock=model.track_flock)
    return [
        pool.submit(
            warm_backend_cache,
            cache,
            root / f"work-{index}",
            {},
            tools=tools,
        )
        for index in range(workers)
    ]


def test_backend_probe_warms_once_for_concurrent_workers(tmp_path: Path) -> None:
    """Workers sharing a cold cache perform one online warm-up."""
    model = _CacheModel()
    with concurrent.futures.ThreadPoolExecutor(max_workers=_CONCURRENT_WORKERS) as pool:
        futures = _submit_warm_ups(pool, model, tmp_path, _CONCURRENT_WORKERS)
        for future in futures:
            future.result(timeout=60)
    assert model.offline_misses == 1, "only one worker may miss the cold cache"
    assert model.online == 1, "concurrent workers must warm the cache once"
    assert model.calls.count(False) == 1, "only the warm-up may run online"
    assert model.probes_while_held == 0, "no probe may run while the lock is held"


@settings(deadline=None, max_examples=10)
@given(
    workers=st.integers(min_value=2, max_value=5),
    delay_ms=st.integers(min_value=0, max_value=50),
)
def test_warm_up_lock_invariant_holds_for_any_worker_count(
    tmp_path_factory: pytest.TempPathFactory,
    workers: int,
    delay_ms: int,
) -> None:
    """Any worker count warms once and probes only while it owns the lock."""
    model = _CacheModel(workers=workers, delay=delay_ms / 1000)
    root = tmp_path_factory.mktemp("warm-up-property")
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = _submit_warm_ups(pool, model, root, workers)
        for future in futures:
            future.result(timeout=120)
    assert model.offline_misses == 1, "one cold probe must serve every worker"
    assert model.online == 1, "a cold cache must warm exactly once"
    assert model.calls.count(False) == 1, "only the warm-up may run online"
    assert model.probes_while_held == 0, (
        "no probe may run while another worker holds the lock"
    )
    assert model.online_without_miss == 0, "an online build must follow a cache miss"
