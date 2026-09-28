"""
Proves the exact bug this fix addresses: without it, a fresh discover_units()
skeleton (a literal `.byte` dump of the ROM) reassembles byte-identical on
the FIRST build - before the model is ever called - and would get marked
"matched" despite zero disassembly having happened. A real 48-hour run
against this bug would finish in seconds with every unit falsely "matched".
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from consoles.nes.plugin import NesPlugin, looks_undisassembled  # noqa: E402
from engine.plugin_base import Unit  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("ca65") is None, reason="cc65 (ca65) not installed"
)


def test_looks_undisassembled_flags_raw_byte_dump():
    text = "\n".join(f"    .byte $aa, $bb, $cc, $dd" for _ in range(20))
    assert looks_undisassembled(text)


def test_looks_undisassembled_does_not_flag_real_code():
    text = """; Main entry point
.segment "CODE"
reset:
    sei
    cld
    lda #$00
    sta $2000
    jmp main_loop

main_loop:
    jmp main_loop
"""
    assert not looks_undisassembled(text)


def _make_unit_from_bank(workdir: Path, bank: bytes, unit_id: str = "prg_bank_00") -> Unit:
    (workdir / "src").mkdir(parents=True, exist_ok=True)
    (workdir / "reference").mkdir(parents=True, exist_ok=True)
    (workdir / "reference" / f"{unit_id}.bin").write_bytes(bank)
    asm_path = workdir / "src" / f"{unit_id}.asm"
    return Unit(unit_id=unit_id, source_paths=[asm_path],
                context={"bank_index": 0, "bank_size": len(bank), "mapper": 0})


def test_fresh_placeholder_skeleton_is_not_matched(tmp_path: Path):
    """This is the regression test for the bug itself."""
    plugin = NesPlugin()
    bank = bytes([0xAA, 0xBB, 0xCC, 0xDD] * 4)
    unit = _make_unit_from_bank(tmp_path, bank)

    # Write exactly what discover_units() would generate as a starting point
    lines = ["; placeholder"]
    for off in range(0, len(bank), 16):
        chunk = bank[off:off + 16]
        lines.append("    .byte " + ", ".join(f"${b:02x}" for b in chunk))
    unit.source_paths[0].write_text("\n".join(lines) + "\n")

    # Fake rom_path - diff_unit only uses its parent.parent to find
    # workdir/reference/ in this skeleton implementation.
    fake_rom_path = tmp_path / "dummy" / "dummy.nes"

    built = plugin.build_unit(unit, tmp_path)
    diff = plugin.diff_unit(unit, built, fake_rom_path)

    assert diff.build_ok
    assert diff.matched_bytes == len(bank)  # bytes DO already match...
    assert not diff.matched  # ...but must NOT be reported as done
    assert diff.needs_disassembly


def test_real_disassembly_with_matching_bytes_counts_as_matched(tmp_path: Path):
    """A unit that's been turned into real instructions AND still assembles
    byte-identical should be accepted - the fix must not block genuine
    completions, only untouched placeholders.
    """
    plugin = NesPlugin()
    # lda #$aa (A9 AA) ; sta $bb (85 BB) ; rts (60) - real, meaningful 6502,
    # deliberately chosen so its assembled bytes match a tiny synthetic bank.
    bank = bytes.fromhex("a9aa 85bb 60".replace(" ", ""))
    unit = _make_unit_from_bank(tmp_path, bank)
    unit.source_paths[0].write_text(
        "; entry point\n"
        ".segment \"CODE\"\n"
        "start:\n"
        "    lda #$aa\n"
        "    sta $bb\n"
        "    rts\n"
    )

    fake_rom_path = tmp_path / "dummy" / "dummy.nes"
    built = plugin.build_unit(unit, tmp_path)
    diff = plugin.diff_unit(unit, built, fake_rom_path)

    assert diff.build_ok, diff.build_error
    assert diff.matched, diff.detail
    assert not diff.needs_disassembly
