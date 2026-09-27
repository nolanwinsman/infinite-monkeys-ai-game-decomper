"""
Time/attempt budgeting lives OUTSIDE the model entirely. Never rely on a
prompt telling the model "work for 24 hours" - a plain script checking
wall-clock time each loop iteration is what actually enforces the limit.
The container's own timeout (see docker/docker-compose.yml) is a backstop
in case this process itself hangs.
"""

from __future__ import annotations

import time


class TimeBudget:
    def __init__(self, hours: float):
        self.deadline = time.time() + hours * 3600

    def expired(self) -> bool:
        return time.time() >= self.deadline

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline - time.time())

    def remaining_human(self) -> str:
        s = int(self.remaining_seconds())
        return f"{s // 3600}h{(s % 3600) // 60}m"


class AttemptBudget:
    """Per-unit retry cap, independent of the overall time budget.

    Without this, the loop could spend the entire time budget hammering one
    impossible unit. MAX_ATTEMPTS_PER_UNIT is deliberately small by default;
    a unit that fails this many times gets marked 'stuck' and the
    orchestrator moves on, coming back to stuck units only if time remains
    after a full pass.
    """

    def __init__(self, max_attempts: int = 8):
        self.max_attempts = max_attempts

    def exhausted(self, attempts_so_far: int) -> bool:
        return attempts_so_far >= self.max_attempts
