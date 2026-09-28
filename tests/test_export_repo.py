from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.export_repo import export_repo, scan_for_embedded_rom_bytes  # noqa: E402


def test_scan_detects_embedded_rom_run():
    rom_bytes = bytes(range(256)) * 4  # 1024 bytes, plenty of unique 32-byte runs
    leaked_chunk = rom_bytes[100:140]  # 40 contiguous bytes lifted verbatim
    file_bytes = b"some preamble text " + leaked_chunk + b" more text"
    hits = scan_for_embedded_rom_bytes(file_bytes, rom_bytes, min_run=32)
    assert hits, "expected the embedded ROM run to be detected"


def test_scan_finds_nothing_in_unrelated_file():
    rom_bytes = bytes(range(256)) * 4
    file_bytes = b"lda #$01\nsta $00\njmp loop\n; totally original comment text here"
    hits = scan_for_embedded_rom_bytes(file_bytes, rom_bytes, min_run=32)
    assert not hits


def test_export_blocks_file_with_embedded_rom_bytes(tmp_path: Path):
    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)
    rom_bytes = bytes(range(256)) * 8
    rom_path = tmp_path / "game.nes"
    rom_path.write_bytes(rom_bytes)

    leaked = rom_bytes[50:90]
    bad_file = workdir / "src" / "leaky.asm"
    bad_file.write_text("; comment\n" + leaked.hex() + "\n")
    # NOTE: .hex() text won't match raw bytes directly - write raw bytes instead
    bad_file.write_bytes(b"; comment\n" + leaked + b"\n")

    good_file = workdir / "src" / "clean.asm"
    good_file.write_text("lda #$01\nsta $00\nrts\n")

    dest = tmp_path / "export"
    report = export_repo(workdir, rom_path, dest)

    exported_names = {p.name for p in report.exported_files}
    assert "clean.asm" in exported_names
    assert "leaky.asm" not in exported_names
    assert any(rel.name == "leaky.asm" for rel, _off in report.embedded_rom_leaks)
    assert not report.clean


def test_export_flags_placeholder_byte_dump_skeleton(tmp_path: Path):
    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)
    rom_path = tmp_path / "game.nes"
    rom_path.write_bytes(bytes(range(256)))

    skeleton = workdir / "src" / "prg_bank_00.asm"
    lines = ["; placeholder"] + [f"    .byte ${i:02x}, ${i:02x}, ${i:02x}, ${i:02x}"
                                  for i in range(50)]
    skeleton.write_text("\n".join(lines))

    dest = tmp_path / "export"
    report = export_repo(workdir, rom_path, dest)

    assert not any(p.name == "prg_bank_00.asm" for p in report.exported_files)
    assert any(p.name == "prg_bank_00.asm" for p in report.flagged_for_review)


def test_export_allows_real_disassembly(tmp_path: Path):
    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)
    rom_path = tmp_path / "game.nes"
    rom_path.write_bytes(bytes(range(256)))

    real = workdir / "src" / "main.asm"
    real.write_text("""; Main entry point
.segment "CODE"
reset:
    sei
    cld
    lda #$00
    sta $2000
    jmp main_loop

main_loop:
    jmp main_loop
""")

    dest = tmp_path / "export"
    report = export_repo(workdir, rom_path, dest)
    assert any(p.name == "main.asm" for p in report.exported_files)
    assert report.clean
