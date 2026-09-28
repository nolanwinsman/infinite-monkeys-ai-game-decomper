from __future__ import annotations

import hashlib
from pathlib import Path

from engine.plugin_base import ConsolePlugin, Unit, UnitDiff

from . import toolchain
from .splitter import slice_banks


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def byte_diff_detail(expected: bytes, actual: bytes, max_context: int = 8) -> tuple[int, str]:
    """Returns (matched_byte_count, human-readable first-mismatch description).

    This is intentionally simple (raw byte compare, not instruction-aware).
    A stronger version would disassemble both sides around the first
    mismatch with a 6502 disassembler and show instruction-level context,
    which is much more useful to a model than raw hex - that's a clear
    upgrade path once this skeleton is validated end-to-end.
    """
    n = min(len(expected), len(actual))
    matched = 0
    first_mismatch = None
    for i in range(n):
        if expected[i] == actual[i]:
            matched += 1
        elif first_mismatch is None:
            first_mismatch = i

    if len(expected) != len(actual):
        detail = f"length mismatch: expected {len(expected)} bytes, got {len(actual)} bytes. "
    else:
        detail = ""

    if first_mismatch is not None:
        lo = max(0, first_mismatch - max_context)
        hi = min(n, first_mismatch + max_context)
        exp_hex = expected[lo:hi].hex(" ")
        act_hex = actual[lo:hi].hex(" ")
        detail += (
            f"first mismatch at offset 0x{first_mismatch:04x}: "
            f"expected [{exp_hex}] got [{act_hex}]"
        )
    elif not detail:
        detail = "exact match"

    return matched, detail


def looks_undisassembled(text: str, threshold: float = 0.5) -> bool:
    """True if this source still looks like our own raw `.byte` placeholder
    skeleton rather than real disassembled instructions.

    Without this check, a unit whose source is a literal, untouched copy of
    the original bytes wrapped in `.byte` directives would reassemble
    byte-identical on the FIRST build - before the model is ever called -
    and get marked "matched" despite zero actual disassembly having
    happened. This is what forces the orchestrator to keep calling the model
    on a unit even once its bytes already match, until it's been turned into
    real, labeled instructions.
    """
    sample = text[:5000]
    lines = sample.splitlines()
    if not lines:
        return False
    byte_directive_lines = sum(1 for line in lines if ".byte" in line)
    return (byte_directive_lines / len(lines)) >= threshold


class NesPlugin(ConsolePlugin):
    name = "nes"
    source_stack = "6502 assembly, ca65 (cc65 suite) syntax"

    def discover_units(self, rom_path: Path, workdir: Path) -> list[Unit]:
        header, prg_banks, chr_banks = slice_banks(rom_path)
        (workdir / "src").mkdir(parents=True, exist_ok=True)
        (workdir / "reference").mkdir(parents=True, exist_ok=True)

        units: list[Unit] = []
        for i, bank in enumerate(prg_banks):
            unit_id = f"prg_bank_{i:02d}"
            ref_path = workdir / "reference" / f"{unit_id}.bin"
            if not ref_path.exists():
                ref_path.write_bytes(bank)

            asm_path = workdir / "src" / f"{unit_id}.asm"
            if not asm_path.exists():
                # Starting skeleton: raw .byte dump. This is a valid but
                # completely unlabeled program - it's the model's job to
                # progressively replace .byte blobs with real instructions/
                # labels while staying byte-identical when reassembled.
                # A real project would seed this with whatever an initial
                # disassembly pass (e.g. da65 from cc65) can already infer.
                lines = [f"; {unit_id} - raw placeholder, {len(bank)} bytes"]
                for off in range(0, len(bank), 16):
                    chunk = bank[off:off + 16]
                    byte_list = ", ".join(f"${b:02x}" for b in chunk)
                    lines.append(f"    .byte {byte_list}")
                asm_path.write_text("\n".join(lines) + "\n")

            units.append(Unit(
                unit_id=unit_id,
                source_paths=[asm_path],
                context={
                    "bank_index": i,
                    "bank_size": len(bank),
                    "mapper": header.mapper,
                },
            ))

        # CHR banks (graphics tiles) are typically not "code" at all for
        # most mappers and don't belong in this agent loop the same way -
        # left out of `units` deliberately. They still need to be included
        # unmodified at final link time; a full plugin would carry them
        # through build_full() as opaque binary includes.
        return units

    def build_unit(self, unit: Unit, workdir: Path) -> Path | None:
        asm_path = unit.source_paths[0]
        build_dir = workdir / "build"
        build_dir.mkdir(parents=True, exist_ok=True)

        out_obj = build_dir / f"{unit.unit_id}.o"
        asm_result = toolchain.assemble(asm_path, out_obj)
        if not asm_result.ok:
            self._last_build_error = asm_result.stderr
            return None

        # A flat, headerless binary comparable byte-for-byte to the raw ROM
        # slice requires actually linking - a bare .o file has its own
        # object-format header/relocation data and will never match raw ROM
        # bytes no matter what the source says. The linker config is
        # generated per-unit because each bank has a different size.
        bank_size = unit.context["bank_size"]
        cfg_path = build_dir / f"{unit.unit_id}.cfg"
        cfg_path.write_text(
            f"MEMORY {{\n"
            f"    BANK: start = $0000, size = ${bank_size:04x}, fill = yes, fillval = $00;\n"
            f"}}\n"
            f"SEGMENTS {{\n"
            f"    CODE: load = BANK, type = ro;\n"
            f"}}\n"
        )

        out_bin = build_dir / f"{unit.unit_id}.bin"
        link_result = toolchain.link([out_obj], cfg_path, out_bin)
        if not link_result.ok:
            self._last_build_error = link_result.stderr
            return None
        return link_result.output_path

    def diff_unit(self, unit: Unit, built_path: Path | None, rom_path: Path) -> UnitDiff:
        ref_path = rom_path.parent.parent / "reference" / f"{unit.unit_id}.bin"
        # In this skeleton the reference lives under workdir/reference/,
        # written by discover_units(); rom_path itself is the whole ROM.
        # Real wiring resolves this path via workdir, passed through
        # unit.context in a fuller implementation.
        expected = ref_path.read_bytes() if ref_path.exists() else b""

        if built_path is None or not built_path.exists():
            return UnitDiff(
                unit_id=unit.unit_id, matched=False, matched_bytes=0,
                total_bytes=len(expected), build_ok=False,
                build_error=getattr(self, "_last_build_error", "build failed or produced no output"),
                detail="assembly/link error - see build_error",
            )

        actual = built_path.read_bytes()
        matched_bytes, detail = byte_diff_detail(expected, actual)
        bytes_match = (matched_bytes == len(expected) == len(actual))

        if bytes_match:
            current_text = unit.source_paths[0].read_text()
            if looks_undisassembled(current_text):
                return UnitDiff(
                    unit_id=unit.unit_id, matched=False, matched_bytes=matched_bytes,
                    total_bytes=len(expected), needs_disassembly=True,
                    detail=(
                        "bytes already match, but this is still an unrefined "
                        "raw .byte placeholder dump, not real disassembly - "
                        "replace it with labeled 6502 instructions"
                    ),
                )

        return UnitDiff(
            unit_id=unit.unit_id, matched=bytes_match, matched_bytes=matched_bytes,
            total_bytes=len(expected), detail=detail,
        )

    def build_full(self, workdir: Path) -> Path | None:
        # Link every matched/current bank back together into a full .nes
        # image, re-attaching the original iNES header and CHR data
        # unchanged. Left as a TODO for the same reason as build_unit's
        # linker-config note above - functionally straightforward, just
        # verbose, and best written against one concrete real ROM.
        raise NotImplementedError("wire this up against a real target ROM")

    def verify_full(self, built_image: Path | None, rom_path: Path) -> bool:
        if built_image is None or not built_image.exists():
            return False
        return sha1(built_image.read_bytes()) == sha1(rom_path.read_bytes())

    def render_prompt(self, unit: Unit, diff: UnitDiff | None) -> str:
        base = (
            f"You are working on a byte-exact disassembly of an NES game.\n"
            f"The ORIGINAL tech stack for this unit is {self.source_stack}. "
            f"Do not write C or any higher-level language - only real, "
            f"reassemblable 6502 assembly in ca65 syntax.\n\n"
            f"Unit: {unit.unit_id} (PRG bank {unit.context.get('bank_index')}, "
            f"{unit.context.get('bank_size')} bytes, mapper {unit.context.get('mapper')}).\n\n"
            f"Current source:\n---\n{unit.source_paths[0].read_text()}\n---\n\n"
        )
        if diff is None:
            return base + "Improve this by replacing .byte blobs with real labeled instructions where you can identify them, while keeping the assembled output byte-identical."

        if not diff.build_ok:
            return base + f"The last attempt FAILED TO ASSEMBLE:\n{diff.build_error}\nFix the syntax error and resubmit."

        if diff.needs_disassembly:
            return base + (
                "The bytes ALREADY assemble to an exact match - but this is "
                "still a raw, unlabeled `.byte` dump, not real disassembly. "
                "Your job is to replace as much of this as you can with real "
                "6502 mnemonics, addressing modes, and labels (subroutine "
                "entry points, loop targets, variable names where you can "
                "infer their purpose), while keeping the assembled output "
                "EXACTLY the same bytes. Leave genuinely ambiguous or "
                "clearly non-code regions (e.g. data tables) as `.byte` if "
                "you can't confidently identify them as instructions - "
                "don't guess wrong just to remove every `.byte` line."
            )

        return base + (
            f"The last attempt assembled but does not match yet "
            f"({diff.matched_bytes}/{diff.total_bytes} bytes matched).\n"
            f"Diff detail: {diff.detail}\n"
            f"Revise the source so the assembled bytes match exactly at that "
            f"location, without breaking bytes that already matched."
        )

    def apply_model_output(self, unit: Unit, model_output: str, workdir: Path) -> None:
        # Expect the model to return only the .asm file contents. A more
        # robust version would ask for a fenced code block and extract it
        # explicitly rather than trusting raw output verbatim.
        unit.source_paths[0].write_text(model_output)
