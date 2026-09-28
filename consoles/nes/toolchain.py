"""
Thin wrapper around ca65/ld65 (the cc65 project's assembler/linker), which is
the closest widely-used, still-maintained tool to "the original tech stack"
for commercial NES games: those games were hand-written 6502 assembly, not
compiled from C. Using ca65 syntax means the "source" this whole pipeline
produces is real, reassemblable 6502 asm, not a C approximation of it.

If a specific game's disassembly community has already settled on a
different assembler (nesasm, asm6, etc.) for compatibility with an existing
project's conventions, swap this module - the plugin interface doesn't care
which assembler is used, only that build_unit() returns real built bytes.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class BuildResult:
    ok: bool
    output_path: Path | None
    stderr: str = ""


def assemble(asm_path: Path, out_obj: Path) -> BuildResult:
    """ca65 source.asm -o out.o"""
    try:
        proc = subprocess.run(
            ["ca65", str(asm_path), "-o", str(out_obj)],
            capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError:
        return BuildResult(ok=False, output_path=None,
                            stderr="ca65 not found - install cc65 in the container image")
    if proc.returncode != 0:
        return BuildResult(ok=False, output_path=None, stderr=proc.stderr)
    return BuildResult(ok=True, output_path=out_obj)


def link(obj_paths: list[Path], cfg_path: Path, out_bin: Path) -> BuildResult:
    """ld65 -C cfg.cfg -o out.bin obj1.o obj2.o ..."""
    try:
        proc = subprocess.run(
            ["ld65", "-C", str(cfg_path), "-o", str(out_bin)] + [str(p) for p in obj_paths],
            capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError:
        return BuildResult(ok=False, output_path=None,
                            stderr="ld65 not found - install cc65 in the container image")
    if proc.returncode != 0:
        return BuildResult(ok=False, output_path=None, stderr=proc.stderr)
    return BuildResult(ok=True, output_path=out_bin)
