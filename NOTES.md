# decomp-agent

An agentic loop that tries to (re)produce a byte-identical, buildable source
tree for a game binary, using **only a local model** (via Ollama) running
inside a **sandboxed, network-isolated container**, bounded by a wall-clock
time budget instead of a token budget.

This is a skeleton / starting point, not a finished tool. It implements the
architecture described in the plan below and a working (if minimal) NES
plugin so you have one real end-to-end path to test the loop against before
investing in harder consoles.

## Core idea

"Decompiling a game" is really two different problems:

1. **Toolchain & scaffolding setup** (mostly manual, done once per game):
   figure out the *original* tech stack the game was built with (this is a
   hard requirement per your instruction — NES/SNES games are almost always
   hand-written assembly, not C, so the target for those consoles is
   byte-exact **reassembled asm**, not decompiled C; PS1/N64/GameCube-era
   games were usually C/C++ built with a specific period compiler, so the
   target there is byte-exact **recompiled C**), get that toolchain running,
   and build a program-to-object splitter plus a verifier.
2. **Function-by-function matching** (the repetitive, agent-loop-friendly
   part): for each function/unit, propose source, compile with the *exact*
   original toolchain, diff against the target bytes, revise, repeat.

This repo's `engine/` is a console-agnostic orchestrator for problem (2). Each
console gets its own small plugin under `consoles/<name>/` that knows how to
build with that console's real original toolchain and how to diff the result.
The engine never knows anything about 6502 or PowerPC — it only knows about
the `ConsolePlugin` interface in `engine/plugin_base.py`.

## Architecture

```
decomp-agent/
  engine/
    plugin_base.py     # ConsolePlugin interface every console module implements
    scoreboard.py       # persistent per-unit match/attempt state (JSON, resumable)
    model_client.py     # thin client for a local Ollama model
    orchestrator.py      # the actual loop: pick unit -> ask model -> verify -> repeat
    budget.py           # wall-clock + iteration budget enforcement
  consoles/
    nes/
      plugin.py          # NesPlugin(ConsolePlugin) - ca65/ld65 based
      splitter.py         # turns a raw .nes ROM into an initial disassembly project
      toolchain.py         # wraps cc65/ca65/ld65 invocation
  docker/
    Dockerfile            # sandboxed agent container (no network egress except to ollama)
    docker-compose.yml    # agent container + ollama sidecar
  tests/
    fixtures/hello.asm    # tiny known-good 6502 program used to sanity-check the verifier
    test_verify.py        # standalone test of the diff/verify logic, no model involved
  run.py                  # CLI entrypoint
```

## Why the verifier comes before the agent

The single most important property of this project is that **the win
condition is unambiguous**: rebuilt binary == original binary, byte for byte
(checked via whole-image SHA1, exactly like re4's `build.sha1`). But
whole-image match gives an agent zero gradient to climb — it's pass/fail
across the entire game. So the verifier does two things:

- `verify_unit()` — compiles/assembles *one* unit (bank, object file,
  function) and returns a structured diff report (matched bytes, mismatched
  bytes, and where possible a decoded instruction-level diff), which is what
  the agent actually iterates against.
- `verify_full()` — the whole-image SHA1 check, used only to report overall
  progress and as the final win condition.

`tests/test_verify.py` exercises this without any model in the loop at all —
run that first, on the tiny fixture, before ever pointing this at a real ROM.

## Two kinds of repo, on purpose

This repo (`decomp-agent`) is the **tool**: the orchestrator, model client,
and per-console plugins. It never gets published with game content in it and
never needs to — it's the thing that *produces* game-specific output.

Each successfully (or partially) decompiled game gets its own **separate,
publishable repo** containing only source code and build tooling, no ROM, no
extracted assets — the same pattern used by re4 and most other matching
decomps. `tools/export_repo.py` builds that output repo from a working
`decomp-agent` workdir and actively scans for mistakes that would undermine
the "no copyrighted material" claim the whole approach depends on:

- blocks any file containing a byte run that also appears verbatim in the
  original ROM (catches, for example, our own placeholder `.byte` skeletons
  before they ever get published)
- flags binary files and files that still look like raw hex dumps rather
  than real disassembly, for manual review instead of silent export
- writes a README for the new repo stating plainly that it requires the
  user's own dump to build

This tool catches mechanical leaks. It does **not** make publishing legally
safe by itself, and especially for Nintendo properties specifically (they
have a well-documented history of being more aggressive about IP enforcement
than most rightsholders in this space) - treat any decision to publish a
public decomp repo as a real legal question, not just an engineering one,
and get an actual lawyer's opinion if you're serious about it.

```bash
python3 tools/export_repo.py \
    --workdir workdir/smb3 --rom /path/to/your/smb3.nes \
    --dest ../smb3-decomp --game-name "Super Mario Bros. 3" --console NES
```

## Dumping your own cartridge

`consoles/nes/dump_nes.py` wraps [INL Retro-Prog](https://gitlab.com/InfiniteNesLives/INL-retro-progdump),
the standard open-source hardware+software NES/Famicom dumper - this needs
the actual USB device connected to your cartridge, there's no software-only
way to read a cart's edge connector. On top of the bare tool, this script
dumps **twice** and byte-diffs the two reads before accepting the result,
since flaky single-bit read errors on real hardware are a real risk you
don't want discovering 20 hours into a run instead of at dump time:

```bash
# SMB3 (USA) is a TLROM board = MMC3 mapper, 256 KiB PRG, 128 KiB CHR
python3 consoles/nes/dump_nes.py --mapper MMC3 --prg-size 256 --chr-size 128 \
    --out workdir/smb3/roms/smb3.nes
```

It reports a SHA1 of the verified dump; cross-checking that against a
preservation database (No-Intro, Redump) yourself before starting a long run
is worth the two minutes it takes.

## Running it

Everything is driven by two scripts. Both build the container image
**locally only** (no registry push, no pulling a prebuilt image of this
project from anywhere) and read model/duration settings from `docker/.env`
(copy `docker/.env.example` to start, or just pass flags and let
`setup.sh` write it for you — see that file for four free model
recommendations across different hardware tiers).

```bash
# One-time: point at your own ROM, get folders created, image built, model
# pulled, and the first run started in the background.
./scripts/setup.sh --rom /path/to/smb3.nes --console nes --name smb3 \
    --model qwen2.5-coder:14b --hours 24

# Watch it work:
tail -f workdir/smb3/logs/run-*.log
cat workdir/smb3/project/scoreboard.json

# If it hits the time limit before finishing (or you just want to give it
# more time later), keep going - safe to call repeatedly, picks up exactly
# where the scoreboard left off:
./scripts/continue.sh --name smb3 --hours 24
```

Under the hood both scripts call the same `docker/entrypoint.sh` inside the
container, which reads `GAME_NAME`/`CONSOLE`/`ROM_FILENAME`/`OLLAMA_MODEL`/
`TIME_LIMIT_HOURS` from the environment and invokes `run.py`. `run.py` writes
`scoreboard.json` after every single unit and holds a PID-based lock on its
workdir (`engine/lock.py`), so `continue.sh` can never accidentally
double-start a second run against an already-in-progress project — calling
it while a run is still active just fails fast with a clear message instead
of corrupting the scoreboard.

Prefer raw commands over the wrapper scripts? The equivalent is:

```bash
docker compose --env-file docker/.env -f docker/docker-compose.yml build agent
docker compose --env-file docker/.env -f docker/docker-compose.yml up -d
docker compose --env-file docker/.env -f docker/docker-compose.yml exec ollama ollama pull qwen2.5-coder:14b
docker compose --env-file docker/.env -f docker/docker-compose.yml exec -d agent /app/docker/entrypoint.sh
```

## Status of the NES plugin

The NES plugin targets **byte-exact reassembled 6502 assembly** using
`ca65`/`ld65` (the cc65 suite), which is the closest thing to "the original
tech stack" for consumer NES games — commercial NES titles were essentially
never written in C. `splitter.py` here is intentionally minimal: it slices
the ROM into PRG/CHR banks and creates one "unit" per bank as a starting
point. A real implementation needs proper code/data separation (most NES
disassembly projects do this by hand or with tools like `mdz`/`iNESticle`/
`disch's NES disassembler`, or through iterative reverse engineering aided by
emulator tracing) — that discovery work is squarely in "Phase 0/1: mostly
manual" territory described in the roadmap, and is stubbed out here rather
than faked.

**Important nuance specific to 6502/NES**: unlike GameCube-style C decomp,
byte-exact reassembly on 6502 is nearly free — a plain disassembler produces
it deterministically by construction, since 6502 machine code has an
essentially 1:1 mapping to mnemonic+addressing-mode. The genuinely hard, and
genuinely AI-loop-worthy, work here is **code/data separation and semantic
labeling** (what's a routine vs. a graphics/level-data table, what should a
variable be named), which has no crisp automatic pass/fail the way "did the
compiler output match" does. Treat byte-exactness as a regression guard on
every change the model makes, not as the main signal of progress.

**Non-recoverable binary data** (graphics tiles, audio samples, compressed
level data that isn't worth "decompiling") should never be embedded as
literal bytes in checked-in source — reference it via an `.incbin`-style
directive that the build pulls from the user's own ROM dump at build time,
exactly like re4's `orig/G4BE08/` + `configure.py` pattern. This repo's CHR
banks are deliberately excluded from `discover_units()`'s agent-facing units
for this reason; wiring the actual `.incbin` extraction into `build_full()`
is one of the concrete next steps below.

## On "going in blind"

If you want the agent to reverse-engineer without leaning on existing public
disassembly/documentation, this repo never retrieves or injects that
material into prompts — `render_prompt()` only ever includes the current
source, the diff, and mechanical context (bank index, size, mapper). One
honest limitation worth knowing: this controls what *this pipeline* feeds
the model, not what the underlying model was pretrained on. A local model's
training data may already include public discussion of well-known games;
there's no way to verify or scrub that from here.

## Next steps (see full roadmap in the planning conversation)

1. Flesh out `splitter.py` with real code/data/vector separation for one real,
   small, well-understood NES game to validate the loop end-to-end.
2. Get `tests/test_verify.py` passing, then run the orchestrator against that
   one real game with a strict per-unit attempt cap (e.g. 5 tries) to check
   the loop behaves sanely before letting it run for hours.
3. Only after the NES path is proven, start a second console plugin for a
   C-toolchain-based system (PS1 or a small GBA homebrew) to validate the
   "C, not asm" branch of the plugin interface.
4. Harden the container: read-only ROM mount, no network egress except to the
   `ollama` service, CPU/time/memory limits at the container level as a
   backstop to the in-process budget.
