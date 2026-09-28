"""
Wraps INL Retro-Prog (https://gitlab.com/InfiniteNesLives/INL-retro-progdump),
the standard open-source hardware+software combo for dumping real NES/Famicom
cartridges. This requires the actual INL Retro-Prog USB device connected to
your cartridge - no software substitute exists for reading a physical cart's
edge connector.

Two things this script adds on top of a bare `inlretro` invocation:

1. Double-dump verification: cheap hardware cart dumps can suffer flaky
   single-bit read errors, especially on old/worn cartridges. Rather than
   trust one read, this dumps the cart TWICE and byte-diffs the results -
   if they disagree, something is wrong (bad connection, worn contacts,
   flaky read) and you should re-seat the cartridge and try again rather
   than start a multi-hour decomp run against a corrupted dump.
2. A structured DumpResult so run.py / the export tool can refuse to proceed
   against an unverified dump.

Usage (SMB3 is a TLROM board = MMC3 mapper, 256KiB PRG + 128KiB CHR):

    python3 dump_nes.py --mapper MMC3 --prg-size 256 --chr-size 128 \\
        --out /work/roms/smb3.nes

If you already know your dump is good (e.g. you verified it against a
known-good hash from a preservation database like No-Intro), you can skip
re-dumping with --skip-verify, but that's an escape hatch, not the default.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DumpResult:
    ok: bool
    rom_path: Path | None
    sha1: str = ""
    detail: str = ""


def sha1_of(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _run_inlretro_dump(mapper: str, prg_size_kb: int, chr_size_kb: int,
                        out_path: Path, inlretro_bin: str = "inlretro") -> None:
    """One raw invocation of the real tool. Raises on failure.

    Flag meanings follow INL Retro-Prog's own CLI (-c console, -m mapper,
    -x PRG size in KiB, -y CHR size in KiB, -d dump-to-file path). Consult
    `inlretro -h` and the mapper-specific script under
    INL-retro-progdump/host/scripts/nes/ for your exact board if this
    doesn't match (mapper scripts and flags have shifted across releases).
    """
    if shutil.which(inlretro_bin) is None:
        raise FileNotFoundError(
            f"'{inlretro_bin}' not found on PATH. Install INL Retro-Prog's host "
            "software from https://gitlab.com/InfiniteNesLives/INL-retro-progdump "
            "and make sure the device is connected."
        )
    cmd = [
        inlretro_bin, "-s", "scripts/inlretro2.lua",
        "-c", "NES", "-m", mapper,
        "-x", str(prg_size_kb), "-y", str(chr_size_kb),
        "-d", str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"inlretro dump failed: {proc.stderr or proc.stdout}")


def dump_and_verify(mapper: str, prg_size_kb: int, chr_size_kb: int,
                     out_path: Path, skip_verify: bool = False) -> DumpResult:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        _run_inlretro_dump(mapper, prg_size_kb, chr_size_kb, out_path)
    except Exception as e:
        return DumpResult(ok=False, rom_path=None, detail=str(e))

    first_bytes = out_path.read_bytes()

    if skip_verify:
        return DumpResult(ok=True, rom_path=out_path, sha1=sha1_of(first_bytes),
                           detail="verification skipped by request")

    second_path = out_path.with_suffix(".verify.nes")
    try:
        _run_inlretro_dump(mapper, prg_size_kb, chr_size_kb, second_path)
    except Exception as e:
        return DumpResult(ok=False, rom_path=out_path,
                           detail=f"first dump succeeded but verification re-dump failed: {e}")

    second_bytes = second_path.read_bytes()
    second_path.unlink(missing_ok=True)

    ok, detail = compare_dumps(first_bytes, second_bytes)
    if not ok:
        return DumpResult(ok=False, rom_path=out_path, detail=detail)

    return DumpResult(ok=True, rom_path=out_path, sha1=sha1_of(first_bytes),
                       detail="two independent reads matched byte-for-byte")


def compare_dumps(a: bytes, b: bytes) -> tuple[bool, str]:
    """Pure logic, no hardware - this is what tests/test_dump_nes.py exercises."""
    if len(a) != len(b):
        return False, f"length mismatch between reads: {len(a)} vs {len(b)} bytes"
    mismatches = [i for i in range(len(a)) if a[i] != b[i]]
    if mismatches:
        first = mismatches[0]
        return False, (
            f"{len(mismatches)} byte(s) disagreed between two reads, first at "
            f"offset 0x{first:04x} (0x{a[first]:02x} vs 0x{b[first]:02x}) - "
            f"re-seat the cartridge and try again"
        )
    return True, "reads matched"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mapper", required=True, help="e.g. MMC3, MMC1, NROM - matches an "
                                                      "INL-retro-progdump scripts/nes/*.lua name")
    ap.add_argument("--prg-size", type=int, required=True, help="PRG-ROM size in KiB")
    ap.add_argument("--chr-size", type=int, required=True, help="CHR-ROM size in KiB")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--skip-verify", action="store_true",
                    help="accept a single read without a confirming re-dump (not recommended)")
    args = ap.parse_args()

    result = dump_and_verify(args.mapper, args.prg_size, args.chr_size, args.out,
                              skip_verify=args.skip_verify)
    if not result.ok:
        sys.exit(f"Dump failed or unverified: {result.detail}")

    print(f"Dump OK: {result.rom_path}")
    print(f"SHA1: {result.sha1}")
    print(f"({result.detail})")
    print(
        "\nNote: this confirms your dump is internally CONSISTENT (two reads "
        "agree), not that it matches a known-good release. If you want to "
        "cross-check against a preservation database (No-Intro, Redump), "
        "compare the SHA1 above yourself before starting a long decomp run."
    )


if __name__ == "__main__":
    main()
