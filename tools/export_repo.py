"""
Turns a working decomp-agent workdir into a clean, publishable per-game repo
that contains source code and build tooling ONLY - no ROM, no extracted
assets, no bytes that came from the original copyrighted binary. This is the
same pattern used by re4 and most other matching decomps: raw/uninterpretable
binary data (graphics, audio, compressed blobs) is never checked in - it's
referenced via an .incbin-style directive that the build pulls from a ROM
the *end user* supplies themselves at build time.

This tool does NOT make publishing legally safe by itself - see the
`--report-only` output and the README section on this for why. It catches
mechanical mistakes (an accidentally-committed ROM, a source file that still
contains a literal hex dump of copyrighted bytes) that would undermine the
"contains no copyrighted material" claim your repo depends on. It cannot
evaluate closer legal questions like substantial similarity of comments,
retained naming schemes, or trademark use - that's a job for an actual
lawyer, not a script.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Extensions that should NEVER appear in an exported repo - these are either
# original ROM images or raw extracted assets, both of which must instead be
# produced from the user's own dump at build time.
FORBIDDEN_EXTENSIONS = {
    ".nes", ".rom", ".iso", ".gcm", ".rvz", ".wbfs", ".n64", ".z64", ".sfc",
    ".smc", ".bin",  # .bin is ambiguous (build output vs. raw asset) -
                      # flagged for manual review rather than auto-excluded
}

# Directories from a workdir that are always working state, never exported.
EXCLUDED_DIR_NAMES = {"reference", "build", "roms", "__pycache__", ".git"}

MIN_LEAK_RUN_LENGTH = 32  # contiguous bytes; see scan_for_embedded_rom_bytes


@dataclass
class ExportReport:
    exported_files: list[Path] = field(default_factory=list)
    skipped_forbidden_ext: list[Path] = field(default_factory=list)
    flagged_for_review: list[Path] = field(default_factory=list)
    embedded_rom_leaks: list[tuple[Path, int]] = field(default_factory=list)  # (file, offset)

    @property
    def clean(self) -> bool:
        return not self.skipped_forbidden_ext and not self.embedded_rom_leaks

    def summary(self) -> str:
        lines = [
            f"Exported: {len(self.exported_files)} file(s)",
            f"Skipped (forbidden extension): {len(self.skipped_forbidden_ext)}",
            f"Flagged for manual review: {len(self.flagged_for_review)}",
            f"Embedded original-ROM byte runs found: {len(self.embedded_rom_leaks)}",
        ]
        return "\n".join(lines)


def scan_for_embedded_rom_bytes(file_bytes: bytes, rom_bytes: bytes,
                                 min_run: int = MIN_LEAK_RUN_LENGTH) -> list[int]:
    """Returns offsets in file_bytes where a long contiguous run also appears
    verbatim in the original ROM - i.e. likely-copied original bytes.

    Deliberately simple (a sliding-window substring search), not a rolling
    hash - fine at the file sizes involved here (source files, not whole
    ROMs). This exists specifically to catch cases like our own placeholder
    `.byte $aa, $bb, ...` skeleton dumps, which are valid *working* state but
    must never make it into a published repo unchanged.
    """
    if len(file_bytes) < min_run:
        return []
    hits = []
    i = 0
    while i <= len(file_bytes) - min_run:
        window = file_bytes[i:i + min_run]
        if window in rom_bytes:
            hits.append(i)
            i += min_run  # skip past this confirmed run rather than re-flag every offset
        else:
            i += 1  # correctness over speed: source files are small (KB), not whole ROMs
    return hits


def _looks_like_hex_byte_dump(text: str) -> bool:
    """Cheap heuristic: does this source file look like our own placeholder
    `.byte $xx, $xx, ...` skeleton rather than real disassembled instructions?
    Used to flag files that likely still need real disasm work before export,
    independent of the raw-byte-matching scan above (which only fires on
    exact byte sequences and would miss e.g. re-encoded but still-meaningless
    dumps).
    """
    sample = text[:5000]
    byte_directive_lines = sum(1 for line in sample.splitlines() if ".byte" in line)
    total_lines = max(1, len(sample.splitlines()))
    return (byte_directive_lines / total_lines) >= 0.5


def export_repo(workdir: Path, rom_path: Path, dest: Path,
                 include_globs: list[str] | None = None) -> ExportReport:
    """Copy an allowlist of source/build files from workdir into dest,
    scanning everything against the original ROM's bytes first.

    include_globs defaults to source and build-config files; deliberately
    does NOT default to "everything in workdir", since workdir also holds
    the reference/ and build/ directories full of exactly the material that
    must never be published.
    """
    include_globs = include_globs or ["src/**/*", "include/**/*", "config/**/*",
                                       "*.md", "*.py", "*.cfg", "LICENSE*"]
    rom_bytes = rom_path.read_bytes()
    report = ExportReport()
    dest.mkdir(parents=True, exist_ok=True)

    candidates: set[Path] = set()
    for pattern in include_globs:
        candidates.update(p for p in workdir.glob(pattern) if p.is_file())

    for src_file in sorted(candidates):
        rel = src_file.relative_to(workdir)
        if any(part in EXCLUDED_DIR_NAMES for part in rel.parts):
            continue
        if src_file.suffix.lower() in FORBIDDEN_EXTENSIONS:
            report.skipped_forbidden_ext.append(rel)
            continue

        data = src_file.read_bytes()
        leak_offsets = scan_for_embedded_rom_bytes(data, rom_bytes)
        if leak_offsets:
            for off in leak_offsets:
                report.embedded_rom_leaks.append((rel, off))
            continue  # do not export files with confirmed embedded original bytes

        try:
            text = data.decode("utf-8")
            if _looks_like_hex_byte_dump(text):
                report.flagged_for_review.append(rel)
                continue  # don't silently export placeholder skeletons
        except UnicodeDecodeError:
            report.flagged_for_review.append(rel)
            continue  # binary file that wasn't caught by extension or byte-scan - be conservative

        out_path = dest / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, out_path)
        report.exported_files.append(rel)

    return report


def write_repo_readme(dest: Path, game_name: str, console: str) -> None:
    readme = dest / "README.md"
    readme.write_text(f"""# {game_name} — decompilation

This repository contains reconstructed source and build tooling for
{game_name} ({console}). It contains **no game assets and no code or data
copied from the original cartridge/disc**. To build it, you need your own
legally-obtained dump of the game; the build process reads specific bytes
from *your* dump at configure/build time and never stores them here.

This follows the same non-distribution pattern used by other decompilation
projects in this space.

## Legal

The reconstructed source is offered for research and preservation purposes.
This repository does not include, and has never included, any copyrighted
assets, ROM/ISO data, or extracted game files.
""")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workdir", required=True, type=Path)
    ap.add_argument("--rom", required=True, type=Path)
    ap.add_argument("--dest", required=True, type=Path)
    ap.add_argument("--game-name", required=True)
    ap.add_argument("--console", required=True)
    args = ap.parse_args()

    report = export_repo(args.workdir, args.rom, args.dest)
    print(report.summary())

    if report.embedded_rom_leaks:
        print("\nBLOCKED - embedded original-ROM byte runs found in:")
        for rel, off in report.embedded_rom_leaks:
            print(f"  {rel} (offset 0x{off:04x})")
        print("\nThese files were NOT exported. Fix them before publishing.")

    if report.flagged_for_review:
        print("\nFlagged for manual review (not exported automatically):")
        for rel in report.flagged_for_review:
            print(f"  {rel}")

    if report.clean and report.exported_files:
        write_repo_readme(args.dest, args.game_name, args.console)
        print(f"\nExported {len(report.exported_files)} file(s) to {args.dest}")
    else:
        sys.exit("\nExport incomplete - resolve flagged/blocked files above before publishing.")


if __name__ == "__main__":
    main()
