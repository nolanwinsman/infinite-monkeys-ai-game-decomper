"""
The loop itself. Everything here is console-agnostic - it only calls
methods on a ConsolePlugin. Swapping consoles means swapping the plugin
instance, nothing in this file changes.

Design choices worth calling out:
- One unit at a time, smallest scope that has a clear pass/fail, so a stuck
  unit costs a handful of attempts, not the whole run.
- The orchestrator (this code), not the model, decides when to stop trying
  a unit and when to stop the whole run. The model only ever answers
  "here is your one task, here is the diff from last time, try again."
- Every attempt is persisted to the scoreboard before moving on, so killing
  the process at any point loses at most one in-flight attempt.
- The run loop exits as soon as no non-stuck units remain, even if time
  budget is left over. An earlier version tried to give stuck units "one
  more shot" by re-including them once the non-stuck pool was empty - but
  _attempt_unit() checks the SAME attempts counter that already hit the cap,
  so a re-included stuck unit gets immediately re-marked stuck without ever
  calling the model. With no model call and no delay in that path, that
  turned into a zero-delay busy loop spinning at full CPU for the rest of
  the time budget (which could be hours, or with a long --time-limit-hours,
  much longer) instead of exiting or doing useful work. If stuck units
  should get another chance, that needs its own bounded mechanism (e.g. a
  single extra pass with a raised attempt cap), not a plain re-queue.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .budget import AttemptBudget, TimeBudget
from .model_client import OllamaClient
from .plugin_base import ConsolePlugin, Unit
from .scoreboard import Scoreboard

log = logging.getLogger("decomp_agent.orchestrator")


class Orchestrator:
    def __init__(
        self,
        plugin: ConsolePlugin,
        rom_path: Path,
        workdir: Path,
        model: OllamaClient,
        scoreboard: Scoreboard,
        time_budget: TimeBudget,
        attempt_budget: AttemptBudget | None = None,
    ):
        self.plugin = plugin
        self.rom_path = rom_path
        self.workdir = workdir
        self.model = model
        self.scoreboard = scoreboard
        self.time_budget = time_budget
        self.attempt_budget = attempt_budget or AttemptBudget()

    def run(self) -> None:
        log.info(
            "Discovering units for %s (%s)",
            self.plugin.name,
            self.plugin.source_stack,
        )

        units: list[Unit] = self.plugin.discover_units(
            self.rom_path,
            self.workdir,
        )

        for u in units:
            self.scoreboard.ensure_unit(u.unit_id)

        self.scoreboard.save()

        by_id = {u.unit_id: u for u in units}

        while not self.time_budget.expired():
            pending = self.scoreboard.pending_units(exclude_stuck=True)

            if not pending:
                log.info(
                    "No pending units remain. %s",
                    self.scoreboard.progress_summary(),
                )
                break

            unit_id = pending[0]
            unit = by_id.get(unit_id)

            if unit is None:
                # Unit known to scoreboard but not returned this run
                # (e.g. discover_units changed) - skip defensively.
                self.scoreboard.mark_stuck(unit_id)
                log.warning(
                    "Unit %s exists in scoreboard but was not discovered; "
                    "marking stuck",
                    unit_id,
                )
                continue

            log.info(
                "Starting unit %s (%s remaining)",
                unit_id,
                self.time_budget.remaining_human(),
            )

            self._attempt_unit(unit)

            log.info(
                "[%s remaining] %s",
                self.time_budget.remaining_human(),
                self.scoreboard.progress_summary(),
            )

        self._final_report()

    def _attempt_unit(self, unit: Unit) -> None:
        rec = self.scoreboard.ensure_unit(unit.unit_id)

        if self.attempt_budget.exhausted(rec.attempts):
            self.scoreboard.mark_stuck(unit.unit_id)
            log.warning("Unit %s exhausted attempt budget, marking stuck", unit.unit_id)
            return

        # Build+diff current state first so the very first prompt already
        # includes a real diff rather than "nothing built yet" boilerplate,
        # if a prior attempt left partial source on disk.
        built = self.plugin.build_unit(unit, self.workdir)
        diff = self.plugin.diff_unit(unit, built, self.rom_path)

        if diff.matched:
            self.scoreboard.record_attempt(
                unit.unit_id, diff.match_ratio, diff.detail, matched=True
            )
            return

        prompt = self.plugin.render_prompt(unit, diff)
        try:
            response = self.model.generate(prompt)
        except Exception as e:  # local model call failed/timed out
            log.error("Model call failed for %s: %s", unit.unit_id, e)
            self.scoreboard.record_attempt(
                unit.unit_id, diff.match_ratio, f"model error: {e}", matched=False
            )
            return

        self.plugin.apply_model_output(unit, response, self.workdir)

        built = self.plugin.build_unit(unit, self.workdir)
        diff = self.plugin.diff_unit(unit, built, self.rom_path)
        self.scoreboard.record_attempt(
            unit.unit_id,
            diff.match_ratio,
            diff.detail,
            matched=diff.matched,
            build_error=not diff.build_ok,
        )

    def _final_report(self) -> None:
        log.info("Run finished. %s", self.scoreboard.progress_summary())
        image = self.plugin.build_full(self.workdir)
        whole_match = self.plugin.verify_full(image, self.rom_path)
        log.info("Whole-image byte-exact match: %s", whole_match)
