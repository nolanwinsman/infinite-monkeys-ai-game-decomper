"""
Run this FIRST, before pointing anything at a real ROM or model:

    cd decomp-agent && python -m pytest tests/test_verify.py -v

It proves the assemble -> link -> byte-diff pipeline is wired correctly
using a tiny, hand-written, known-good 6502 program, with zero AI involved.
If this doesn't pass, nothing built on top of it can be trusted, no matter
how the agent loop behaves.

Requires cc65 (ca65/ld65) installed. See docker/Dockerfile for the intended
environment - these tests are meant to run inside that container, not
necessarily on your host.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from consoles.nes import toolchain  # noqa: E402
from consoles.nes.plugin import byte_diff_detail  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.skipif(
    shutil.which("ca65") is None or shutil.which("ld65") is None,
    reason="cc65 (ca65/ld65) not installed - see docker/Dockerfile",
)


def test_assemble_hello_succeeds(tmp_path: Path):
    out_obj = tmp_path / "hello.o"
    result = toolchain.assemble(FIXTURES / "hello.asm", out_obj)
    assert result.ok, result.stderr
    assert out_obj.exists()


def test_link_hello_produces_expected_bytes(tmp_path: Path):
    out_obj = tmp_path / "hello.o"
    out_bin = tmp_path / "hello.bin"

    asm_result = toolchain.assemble(FIXTURES / "hello.asm", out_obj)
    assert asm_result.ok, asm_result.stderr

    link_result = toolchain.link([out_obj], FIXTURES / "hello.cfg", out_bin)
    assert link_result.ok, link_result.stderr

    produced = out_bin.read_bytes()
    # lda #$01 (A9 01) ; sta $00 (85 00) ; loop: jmp loop (4C 04 00, since
    # `loop` is the 5th byte of the segment, offset 0x0004)
    assert produced[:7] == bytes.fromhex("a9018500 4c0400".replace(" ", ""))


def test_byte_diff_detects_exact_match():
    data = bytes.fromhex("a9018500")
    matched, detail = byte_diff_detail(data, data)
    assert matched == len(data)
    assert "exact match" in detail


def test_byte_diff_detects_mismatch_and_reports_offset():
    expected = bytes.fromhex("a9018500")
    actual = bytes.fromhex("a9028500")  # second byte differs: 01 vs 02
    matched, detail = byte_diff_detail(expected, actual)
    assert matched == len(expected) - 1
    assert "0x0001" in detail


def test_byte_diff_detects_length_mismatch():
    expected = bytes.fromhex("a9018500")
    actual = bytes.fromhex("a90185")
    matched, detail = byte_diff_detail(expected, actual)
    assert "length mismatch" in detail
