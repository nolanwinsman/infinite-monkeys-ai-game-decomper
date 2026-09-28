"""
Only the pure logic is tested here - actual cartridge dumping requires a
physical INL Retro-Prog device and cannot be exercised in CI or a sandbox.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from consoles.nes.dump_nes import compare_dumps, sha1_of  # noqa: E402


def test_identical_dumps_pass():
    data = bytes([0xAA, 0xBB, 0xCC, 0xDD])
    ok, detail = compare_dumps(data, data)
    assert ok
    assert "matched" in detail


def test_mismatched_byte_is_caught_with_offset():
    a = bytes([0xAA, 0xBB, 0xCC, 0xDD])
    b = bytes([0xAA, 0xBB, 0xCE, 0xDD])  # differs at offset 2
    ok, detail = compare_dumps(a, b)
    assert not ok
    assert "0x0002" in detail


def test_length_mismatch_is_caught():
    a = bytes([0xAA, 0xBB, 0xCC])
    b = bytes([0xAA, 0xBB])
    ok, detail = compare_dumps(a, b)
    assert not ok
    assert "length mismatch" in detail


def test_sha1_is_deterministic():
    data = b"some rom bytes"
    assert sha1_of(data) == sha1_of(data)
    assert sha1_of(data) != sha1_of(data + b"x")
