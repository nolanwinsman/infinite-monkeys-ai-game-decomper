"""
The contract every console module implements. The orchestrator only ever
talks to these methods - it has no idea what a 6502 opcode or a PowerPC
relocation is. That knowledge lives entirely in consoles/<name>/plugin.py.

A "unit" is deliberately abstract: for NES it might be one bank, for a
C-based console it might be one object file or one function. Keeping units
small is what makes the agent loop tractable - each retry is cheap and the
model's context stays focused.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class UnitDiff:
    """Structured result of comparing one built unit against the original.

    matched_bytes / total_bytes give the orchestrator a partial-credit signal
    even when a unit isn't fully matching yet, which is what lets the model
    tell "getting closer" from "no progress" between attempts.
    """

    unit_id: str
    matched: bool
    matched_bytes: int
    total_bytes: int
    # Human/model-readable explanation of the first mismatch: e.g. decoded
    # instruction-level diff, "expected byte 0x4C got 0x20 at offset 0x12",
    # or a compiler/assembler error message if the build itself failed.
    detail: str = ""
    build_ok: bool = True
    build_error: str = ""
    # True when the bytes ALREADY match but the source is still an
    # unrefined placeholder (e.g. a raw `.byte` dump) rather than real
    # disassembled instructions. Byte-exactness alone is trivially true for
    # a literal copy of the original bytes - a plugin should set `matched`
    # to False whenever this is True, so the orchestrator still invokes the
    # model to do the actual (semantic) work instead of treating an untouched
    # placeholder as "done".
    needs_disassembly: bool = False

    @property
    def match_ratio(self) -> float:
        if self.total_bytes == 0:
            return 0.0
        return self.matched_bytes / self.total_bytes


@dataclass
class Unit:
    """One piece of work the agent loop can attempt."""

    unit_id: str
    # Path to the current best-known source file(s) for this unit.
    source_paths: list[Path]
    # Freeform context handed to the model: original source-language hint,
    # symbol names, sizes, neighboring matched code, etc. Built by the
    # plugin, not the engine.
    context: dict = field(default_factory=dict)


class ConsolePlugin(ABC):
    """Implement one of these per console. See consoles/nes/plugin.py."""

    name: str = "unknown-console"
    # The *original* tech stack for this console/era - drives prompting and
    # sets expectations correctly (assembly vs. C, which compiler, etc).
    # e.g. "6502 assembly (ca65-compatible)" or
    #      "C (SN Systems ProDG GCC 2.95.3)"
    source_stack: str = "unknown"

    @abstractmethod
    def discover_units(self, rom_path: Path, workdir: Path) -> list[Unit]:
        """Split the ROM into an initial list of units to work on.

        First call on a fresh workdir should also materialize a starting
        source skeleton (disassembly, stub headers, linker script, etc.)
        under workdir. Subsequent calls should just re-read existing state.
        """

    @abstractmethod
    def build_unit(self, unit: Unit, workdir: Path) -> Path | None:
        """Build just this unit with the real, original toolchain.

        Returns the path to the built binary/object for this unit, or None
        if the build failed (see UnitDiff.build_error for why, which the
        plugin should also have logged).
        """

    @abstractmethod
    def diff_unit(self, unit: Unit, built_path: Path | None, rom_path: Path) -> UnitDiff:
        """Compare a built unit against the corresponding slice of the ROM."""

    @abstractmethod
    def build_full(self, workdir: Path) -> Path | None:
        """Build the entire current source tree into a final image."""

    @abstractmethod
    def verify_full(self, built_image: Path | None, rom_path: Path) -> bool:
        """Whole-image byte-exact check (e.g. SHA1 match), the real win condition."""

    @abstractmethod
    def render_prompt(self, unit: Unit, diff: UnitDiff | None) -> str:
        """Build the prompt for the local model for one attempt at this unit.

        Must make the original tech stack explicit (assembly vs C, dialect,
        target CPU/compiler) so the model doesn't default to writing modern
        C for a 6502 game, or vice versa.
        """

    @abstractmethod
    def apply_model_output(self, unit: Unit, model_output: str, workdir: Path) -> None:
        """Parse the model's response and write it back to unit.source_paths."""
