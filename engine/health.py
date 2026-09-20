"""Operational health rules (alert throttling, stale heartbeats, failure spikes). Pure Python."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


class Throttle:
    """Lets an alert key through at most once per cooldown, so a persistent problem doesn't spam you."""

    def __init__(self, default_cooldown: float = 600.0) -> None:
        self.default = default_cooldown
        self._last: dict[str, float] = {}

    def allow(self, key: str, now: float, cooldown: float | None = None) -> bool:
        window = self.default if cooldown is None else cooldown
        last = self._last.get(key)
        if last is not None and now - last < window:
            return False
        self._last[key] = now
        return True


@dataclass(frozen=True)
class Problem:
    key: str
    text: str


def stale_processes(expected: Iterable[str], ages: dict[str, float], max_age: float) -> list[Problem]:
    """`ages` = seconds since each process's last heartbeat. Missing = never reported."""
    problems = []
    for name in expected:
        if name not in ages:
            problems.append(Problem(name, f"'{name}' has never reported a heartbeat: is it running?"))
        elif ages[name] > max_age:
            problems.append(Problem(name, f"'{name}' has been silent for {int(ages[name])}s: it may have crashed."))
    return problems


def failure_spike(attempted: int, failed: int, min_sample: int = 5, ratio: float = 0.5) -> bool:
    """True when at least `ratio` of a meaningful number of copy attempts failed."""
    return attempted >= min_sample and failed / attempted >= ratio