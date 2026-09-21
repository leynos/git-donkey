"""Pytest configuration shared by the whole suite.

The Hypothesis profile is registered here rather than on the individual
property tests, because the library default of a 200 millisecond deadline is
incompatible with any property that touches a subprocess: every property in
this repository builds a repository or runs a Git command, so a slow machine
would fail the deadline rather than a defect. ``HYPOTHESIS_PROFILE=nightly``
selects the wider profile for a scheduled run; the default keeps the local
suite quick. Both profiles suppress the ``too_slow`` health check for the same
reason: it measures how long an example takes to generate, and generating one
here spawns Git.

See ``docs/developers-guide.md`` under ``## Test infrastructure``.
"""

from __future__ import annotations

import os

from hypothesis import HealthCheck, settings

settings.register_profile(
    "default",
    deadline=None,
    max_examples=50,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile(
    "nightly",
    deadline=None,
    max_examples=500,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))
