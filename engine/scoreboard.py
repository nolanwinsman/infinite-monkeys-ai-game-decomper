"""
Persistent record of per-unit progress so a run can be stopped and resumed,
and so you can watch progress across a long unattended run without reading
logs. This is plain JSON on purpose - no DB dependency, easy to inspect
by hand or tail with `watch cat scoreboard.json`.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class UnitRecord:
    unit_id: str
    status: str = "pending"  # pending | in_progress | matched | stuck | build_error
    attempts: int = 0
    best_match_ratio: float = 0.0
    last_detail: str = ""
    last_attempt_ts: float = 0.0
    matched_ts: float | None = None


@dataclass
class Scoreboard:
    path: Path
    console: str = ""
    rom_sha1: str = ""
    started_ts: float = field(default_factory=time.time)
    units: dict[str, UnitRecord] = field(default_factory=dict)

    @classmethod
    def load_or_create(cls, path: Path, console: str, rom_sha1: str) -> "Scoreboard":
        if path.exists():
            data = json.loads(path.read_text())
            sb = cls(path=path, console=data["console"], rom_sha1=data["rom_sha1"],
                      started_ts=data["started_ts"])
            sb.units = {k: UnitRecord(**v) for k, v in data["units"].items()}
            return sb
        return cls(path=path, console=console, rom_sha1=rom_sha1)

    def ensure_unit(self, unit_id: str) -> UnitRecord:
        if unit_id not in self.units:
            self.units[unit_id] = UnitRecord(unit_id=unit_id)
        return self.units[unit_id]

    def record_attempt(self, unit_id: str, match_ratio: float, detail: str,
                        matched: bool, build_error: bool = False) -> None:
        rec = self.ensure_unit(unit_id)
        rec.attempts += 1
        rec.last_attempt_ts = time.time()
        rec.last_detail = detail
        rec.best_match_ratio = max(rec.best_match_ratio, match_ratio)
        if matched:
            rec.status = "matched"
            rec.matched_ts = time.time()
        elif build_error:
            rec.status = "build_error"
        else:
            rec.status = "in_progress"
        self.save()

    def mark_stuck(self, unit_id: str) -> None:
        self.ensure_unit(unit_id).status = "stuck"
        self.save()

    def pending_units(self, exclude_stuck: bool = False) -> list[str]:
        statuses = {"pending", "in_progress", "build_error"}
        if not exclude_stuck:
            statuses.add("stuck")
        return [uid for uid, rec in self.units.items() if rec.status in statuses]

    def progress_summary(self) -> str:
        total = len(self.units)
        matched = sum(1 for r in self.units.values() if r.status == "matched")
        stuck = sum(1 for r in self.units.values() if r.status == "stuck")
        pct = (matched / total * 100) if total else 0.0
        return f"{matched}/{total} units matched ({pct:.1f}%), {stuck} stuck"

    def save(self) -> None:
        data = {
            "console": self.console,
            "rom_sha1": self.rom_sha1,
            "started_ts": self.started_ts,
            "units": {k: asdict(v) for k, v in self.units.items()},
        }
        self.path.write_text(json.dumps(data, indent=2))
