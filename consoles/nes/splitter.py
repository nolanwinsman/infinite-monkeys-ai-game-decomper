"""
Deliberately minimal. This does the mechanical part (parse the iNES header,
slice PRG/CHR banks) and stops there. It does NOT attempt automatic
code/data separation, which for real commercial NES games is genuinely hard
and is usually done by a human with an emulator + tracer (find execution
addresses, mark code vs. graphics/data tables, identify bank-switching
scheme) or by iteratively feeding disassembler output back through this
same agent loop once a first crude split exists.

Treating "one bank = one unit" is a reasonable starting granularity: it's
small enough for a model to hold in context, and PRG-ROM banks are a natural
seam in how NES games are organized (especially on mappers with fixed/
switchable banks).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

INES_HEADER_SIZE = 16
PRG_BANK_SIZE = 16 * 1024  # 16 KiB, per iNES spec
CHR_BANK_SIZE = 8 * 1024   # 8 KiB, per iNES spec


@dataclass
class InesHeader:
    prg_banks: int
    chr_banks: int
    mapper: int
    has_trainer: bool


def parse_ines_header(rom_bytes: bytes) -> InesHeader:
    if rom_bytes[0:4] != b"NES\x1a":
        raise ValueError("Not an iNES ROM (missing 'NES\\x1a' magic)")
    prg_banks = rom_bytes[4]
    chr_banks = rom_bytes[5]
    flags6 = rom_bytes[6]
    flags7 = rom_bytes[7]
    mapper = (flags7 & 0xF0) | (flags6 >> 4)
    has_trainer = bool(flags6 & 0x04)
    return InesHeader(prg_banks=prg_banks, chr_banks=chr_banks, mapper=mapper,
                       has_trainer=has_trainer)


def slice_banks(rom_path: Path) -> tuple[InesHeader, list[bytes], list[bytes]]:
    """Returns (header, prg_bank_bytes_list, chr_bank_bytes_list)."""
    data = rom_path.read_bytes()
    header = parse_ines_header(data)
    offset = INES_HEADER_SIZE
    if header.has_trainer:
        offset += 512

    prg_banks = []
    for i in range(header.prg_banks):
        prg_banks.append(data[offset:offset + PRG_BANK_SIZE])
        offset += PRG_BANK_SIZE

    chr_banks = []
    for i in range(header.chr_banks):
        chr_banks.append(data[offset:offset + CHR_BANK_SIZE])
        offset += CHR_BANK_SIZE

    return header, prg_banks, chr_banks
