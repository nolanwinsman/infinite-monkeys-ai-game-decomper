# Goals & Roadmap

This document is the working plan for `decomp-agent`. It's meant to be
updated as decisions get made or revised — treat it as a living record of
*why* things are built the way they are, not just a one-time pitch.

## The goal

Build a system that decompiles game binaries back into buildable source,
using:

- **Local models only** (via Ollama), so runtime cost is bounded by
  hardware/time, not token spend.
- **A byte-exact win condition**: rebuilding the produced source with the
  game's *original* toolchain must reproduce the original binary exactly.
- **A sandboxed container**, isolated from the network except for talking to
  the local model, so the agent loop can run unattended for hours without
  being able to do anything outside its own workdir.
- **A wall-clock time budget** (e.g. 24 hours) enforced by code outside the
  model, not by asking the model to police its own time.
- **One skill/plugin per console** (NES first, then others), sharing a
  common orchestrator but each implementing its own toolchain and verifier,
  because "the original tech stack" is a hard requirement and it's
  completely different per console/era (hand-written 6502/65816 assembly
  for NES/SNES; period C/C++ compilers — SN Systems, CodeWarrior, GCC
  variants — for PS1/N64/GameCube-era games).
- **Per-game output repos that contain zero copyrighted IP**, so successful
  decomps can be shared publicly: no ROM, no extracted assets, source and
  build tooling only, following the same pattern used by projects like the
  RE4 decomp this whole idea started from.

## Why this splits into two different problems

"Decompile a game" isn't one task. It's:

1. **Toolchain & scaffolding setup** — mostly manual, done once per game:
   identify the exact original compiler/assembler, get it running, build a
   splitter that turns the ROM into an initial source skeleton, and build a
   verifier that can tell match from non-match. This is investigative work
   and doesn't parallelize well across an agent loop.
2. **Function/unit-level matching** — the repetitive part: for one small
   unit (a bank, an object file, a function), propose source, build with the
   *real* original toolchain, diff against the target bytes, revise, repeat.
   This is what the local-model agent loop is actually for.

The local model's job is (2), never (1). Confusing the two is the biggest
way this kind of project goes off the rails.

## Architecture

```
decomp-agent/                  <- the tool. Never published with game content.
  engine/                      <- console-agnostic: orchestrator, scoreboard,
                                   time/attempt budgets, local-model client
  consoles/<name>/             <- one plugin per console: toolchain wrapper,
                                   ROM splitter, byte-diff logic, prompting
  tools/export_repo.py         <- builds a clean, IP-free per-game repo from
                                   a working decomp-agent workdir
  docker/                      <- sandboxed agent container + local Ollama
                                   sidecar, network-isolated except for that
  tests/                       <- verifier and export-tool tests that run
                                   with zero AI involved
```

Each successfully (or partially) decompiled game becomes its **own separate
repo** — the output of `tools/export_repo.py` — not a folder inside this
one.

## Design decisions made so far, and why

| Decision | Reasoning |
|---|---|
| Orchestrator, scoreboard, budget stay in **Python**, not Go | The loop is I/O-bound (waiting on the local model, waiting on the compiler subprocess); a compiled language wouldn't speed up total decomp time. Revisit this per-plugin later if a console's diff/disassembly engine turns out to be genuinely CPU-bound at scale (this is exactly why real GameCube/Wii decomp tooling like `decomp-toolkit` is written in Rust). |
| Time/attempt budgeting lives in code, not in the prompt | A plain script checking wall-clock time each loop iteration is reliable; asking a model to "work for 24 hours" is not. The container's own timeout is a backstop in case the process itself hangs. |
| Verification is per-unit, not just whole-image | Whole-image SHA1 match is the real win condition but gives zero gradient to climb — an agent can't tell "close" from "nowhere near." Per-unit diffs (matched byte count + first-mismatch detail) are what the model actually iterates against. |
| NES targets **reassembled 6502 assembly**, not decompiled C | Commercial NES games were hand-written assembly, not compiled from C. "Original tech stack" per console is a hard requirement, so the NES plugin's job is disassembly-with-labels, not C reconstruction. |
| Byte-exactness on 6502 is treated as a regression guard, not the main signal | Unlike GameCube-era C matching (a genuine, hard search — did this specific old compiler happen to emit these exact bytes), 6502 machine code has an essentially 1:1 mapping to mnemonic+addressing-mode. A plain disassembler gets byte-exact output almost for free. The actual hard, AI-worthy problem on NES is **code/data separation and meaningful labeling**, which has no automatic pass/fail. |
| Non-recoverable binary data (graphics, audio, compressed level data) is never embedded in checked-in source | Referenced via an `.incbin`-style directive pulled from the *user's own* ROM dump at build time — same pattern as RE4's `orig/G4BE08/` + `configure.py`. This is what makes the exported repo actually free of copyrighted bytes rather than just claiming to be. |
| `tools/export_repo.py` scans for leaks mechanically | Catches accidental embedded ROM bytes (e.g. our own placeholder `.byte` skeletons) and flags files that still look like raw hex dumps rather than real disassembly. This does **not** make publishing legally safe by itself — see Legal below. |
| Cartridge dumps are verified by dumping twice and diffing | Cheap hardware reads can have flaky single-bit errors; catching that at dump time is much cheaper than discovering it after a multi-hour run. |
| Two commands only: `setup.sh` (first run) and `continue.sh` (resume) | Both call the same container-side `docker/entrypoint.sh`, which reads config from `docker/.env` — one place for game/console/model/duration settings instead of long CLI invocations to remember. |
| A PID-based lock (`engine/lock.py`) guards each workdir | `continue.sh` is safe to call even if a previous run hasn't actually finished yet — it fails fast with a clear message instead of two processes corrupting the same `scoreboard.json`. |
| Images are built locally only (`build:`, no `image:` pointing at a registry) | Nothing about this project is pushed or pulled from a registry beyond public base images (Python, Ollama) — the container is yours end to end. |
| No external disassembly/documentation fed into prompts ("going in blind") | `render_prompt()` only includes current source, diff, and mechanical context (bank index, size, mapper) — never retrieved reference material. Known limitation: this controls what *this pipeline* feeds the model, not what the underlying model was already pretrained on; there's no way to verify or scrub a local model's own training data from here. |

## Legal posture (not legal advice)

The "zero IP in the repo" pattern is a real, established community norm —
several well-known decomp projects (including RE4) work this way, and it's
the right engineering default regardless of legal outcome. But:

- The mechanism is specifically "no copyrighted bytes checked in **and** the
  build requires the user's own dump to produce anything," not just "no ROM
  file in the repo."
- This is an untested legal theory in some respects, not settled law.
- Nintendo specifically has a well-documented history of enforcing IP more
  aggressively than most rightsholders in this space.
- `export_repo.py` catches mechanical mistakes. It cannot evaluate closer
  questions (substantial similarity of comments, retained naming schemes,
  trademark use in a repo name/description, etc.).

**If SMB3 (or any other Nintendo title) decomp is going to be published
publicly, treat that as a real legal decision and get an actual lawyer's
opinion before publishing — not after.**

## Current pilot: Super Mario Bros. 3 (NES)

- Cartridge: TLROM board, MMC3 (iNES mapper 4), 256 KiB PRG-ROM, 128 KiB
  CHR-ROM — confirmed against public hardware/mapper documentation, to be
  reconfirmed against the actual dump's header once it exists.
- Dumping: `consoles/nes/dump_nes.py`, wrapping INL Retro-Prog, with
  double-dump verification before accepting a ROM as good.
- Approach: going in blind — no existing public SMB3 disassembly/RAM-map
  material is fed to the model, by choice, with the caveat above about the
  model's own pretraining noted and accepted.
- Status: verifier and splitter proven against synthetic test data
  (`tests/`, all passing against a real `cc65` install). Not yet run against
  the real cartridge dump or a live model.

## Open items / next steps

1. Dump the cartridge with `dump_nes.py`, confirm the header matches the
   TLROM/MMC3/256+128 KiB expectation above (or update this doc if it
   doesn't).
2. Wire up `build_full()` for the NES plugin: relink matched banks, reattach
   the original iNES header, and reattach CHR data via the `.incbin` pattern
   rather than embedding it.
3. Run the orchestrator against the real dump with a live Ollama model and a
   tight per-unit attempt cap (5–8), to see how the loop actually behaves
   before trusting it with an unattended 24-hour run.
4. Only after the NES path is validated end-to-end, start a second plugin
   for a C-toolchain-based console (a small PS1 title or GBA homebrew before
   attempting anything GameCube-scale) to validate the "real compiler, not
   assembly" branch of the plugin interface.
5. Revisit the "going in blind" decision if progress stalls badly — this is
   a call to make deliberately if it comes up, not to quietly abandon.
