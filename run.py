#!/usr/bin/env python3
"""
Entry point. Deliberately dumb - all real logic lives in engine/ and
consoles/. This just wires CLI args to an Orchestrator and runs it.

Example:
    python run.py --console nes --rom /work/roms/game.nes \\
        --workdir /work/project --model qwen2.5-coder:14b \\
        --time-limit-hours 24
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from pathlib import Path

from engine.budget import AttemptBudget, TimeBudget
from engine.lock import AlreadyRunningError, RunLock
from engine.model_client import OllamaClient
from engine.orchestrator import Orchestrator
from engine.scoreboard import Scoreboard

CONSOLE_PLUGINS = {
    "nes": "consoles.nes.plugin.NesPlugin",
    # Add more as they're built, e.g.:
    # "snes": "consoles.snes.plugin.SnesPlugin",
    # "gamecube": "consoles.gamecube.plugin.GameCubePlugin",
}


def load_plugin(name: str):
    print(f"[PLUGIN] Loading console plugin: {name}", flush=True)

    if name not in CONSOLE_PLUGINS:
        sys.exit(f"Unknown console '{name}'. Available: {list(CONSOLE_PLUGINS)}")

    module_path, class_name = CONSOLE_PLUGINS[name].rsplit(".", 1)

    print(f"[PLUGIN] Importing {module_path}.{class_name}", flush=True)

    import importlib

    module = importlib.import_module(module_path)
    plugin = getattr(module, class_name)()

    print(f"[PLUGIN] Successfully loaded {class_name}", flush=True)

    return plugin


def main():
    print("=" * 70, flush=True)
    print("  AI GAME DECOMPILATION RUNNER", flush=True)
    print("=" * 70, flush=True)

    print("[ARGS] Parsing command line arguments...", flush=True)

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--console", required=True, choices=list(CONSOLE_PLUGINS))
    ap.add_argument("--rom", required=True, type=Path)
    ap.add_argument("--workdir", required=True, type=Path)
    ap.add_argument("--model", default="qwen2.5-coder:14b")
    ap.add_argument(
        "--ollama-host",
        default=None,
        help="defaults to $OLLAMA_HOST or http://ollama:11434",
    )
    ap.add_argument("--time-limit-hours", type=float, default=24.0)
    ap.add_argument("--max-attempts-per-unit", type=int, default=8)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    print("[ARGS] Configuration:", flush=True)
    print(f"       Console:             {args.console}", flush=True)
    print(f"       ROM:                 {args.rom}", flush=True)
    print(f"       Work directory:      {args.workdir}", flush=True)
    print(f"       Model:               {args.model}", flush=True)
    print(f"       Ollama host:         {args.ollama_host or 'default'}", flush=True)
    print(f"       Time limit:          {args.time_limit_hours} hours", flush=True)
    print(f"       Max attempts/unit:   {args.max_attempts_per_unit}", flush=True)
    print(f"       Verbose logging:     {args.verbose}", flush=True)

    print("[LOG] Configuring logging...", flush=True)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # ------------------------------------------------------------------
    # Validate ROM
    # ------------------------------------------------------------------

    print("[ROM] Checking ROM...", flush=True)

    if not args.rom.exists():
        sys.exit(f"ROM not found: {args.rom}")

    if not args.rom.is_file():
        sys.exit(f"ROM path is not a file: {args.rom}")

    rom_size = args.rom.stat().st_size

    print(f"[ROM] ROM found: {args.rom}", flush=True)
    print(f"[ROM] ROM size: {rom_size:,} bytes", flush=True)

    # ------------------------------------------------------------------
    # Work directory + run lock
    # ------------------------------------------------------------------

    print("[WORKDIR] Preparing work directory...", flush=True)

    args.workdir.mkdir(parents=True, exist_ok=True)

    print(f"[WORKDIR] Using: {args.workdir}", flush=True)

    print("[LOCK] Acquiring run lock...", flush=True)

    lock = RunLock(args.workdir)
    try:
        lock.acquire()
    except AlreadyRunningError as e:
        sys.exit(str(e))

    print(
        "[LOCK] Lock acquired. (Released automatically on exit, even on a "
        "crash or kill - see engine/lock.py.)",
        flush=True,
    )

    try:
        _run(args)
    finally:
        lock.release()
        print("[LOCK] Lock released.", flush=True)


def _run(args: argparse.Namespace) -> None:
    # ------------------------------------------------------------------
    # ROM hash
    # ------------------------------------------------------------------

    print("[ROM] Calculating SHA-1 hash...", flush=True)

    rom_sha1 = hashlib.sha1(args.rom.read_bytes()).hexdigest()

    print(f"[ROM] SHA-1: {rom_sha1}", flush=True)

    # ------------------------------------------------------------------
    # Scoreboard
    # ------------------------------------------------------------------

    scoreboard_path = args.workdir / "scoreboard.json"

    print("[SCOREBOARD] Loading scoreboard...", flush=True)
    print(f"[SCOREBOARD] Path: {scoreboard_path}", flush=True)

    scoreboard_exists = scoreboard_path.exists()

    if scoreboard_exists:
        print("[SCOREBOARD] Existing scoreboard found.", flush=True)
    else:
        print("[SCOREBOARD] No existing scoreboard. Creating new one.", flush=True)

    scoreboard = Scoreboard.load_or_create(
        scoreboard_path,
        args.console,
        rom_sha1,
    )

    print("[SCOREBOARD] Scoreboard loaded successfully.", flush=True)

    if scoreboard.rom_sha1 != rom_sha1:
        sys.exit(
            "Existing scoreboard was built against a different ROM "
            f"(expected sha1 {scoreboard.rom_sha1}, got {rom_sha1}). "
            "Use a fresh --workdir for a different ROM."
        )

    print("[SCOREBOARD] ROM hash matches.", flush=True)

    # ------------------------------------------------------------------
    # Plugin
    # ------------------------------------------------------------------

    plugin = load_plugin(args.console)

    # ------------------------------------------------------------------
    # Ollama
    # ------------------------------------------------------------------

    print("[MODEL] Initializing Ollama client...", flush=True)
    print(f"[MODEL] Model: {args.model}", flush=True)

    if args.ollama_host:
        print(f"[MODEL] Host: {args.ollama_host}", flush=True)
    else:
        print("[MODEL] Host: using default Ollama configuration", flush=True)

    model = OllamaClient(
        model=args.model,
        host=args.ollama_host,
    )

    print("[MODEL] Ollama client initialized.", flush=True)

    # ------------------------------------------------------------------
    # Budgets
    # ------------------------------------------------------------------

    print("[BUDGET] Creating time budget...", flush=True)

    time_budget = TimeBudget(hours=args.time_limit_hours)

    print(f"[BUDGET] Time limit: {args.time_limit_hours} hours", flush=True)

    print("[BUDGET] Creating attempt budget...", flush=True)

    attempt_budget = AttemptBudget(max_attempts=args.max_attempts_per_unit)

    print(
        f"[BUDGET] Max attempts per unit: {args.max_attempts_per_unit}",
        flush=True,
    )

    # ------------------------------------------------------------------
    # Orchestrator
    # ------------------------------------------------------------------

    print("[ORCHESTRATOR] Creating orchestrator...", flush=True)

    orchestrator = Orchestrator(
        plugin=plugin,
        rom_path=args.rom,
        workdir=args.workdir,
        model=model,
        scoreboard=scoreboard,
        time_budget=time_budget,
        attempt_budget=attempt_budget,
    )

    print("[ORCHESTRATOR] Orchestrator created.", flush=True)

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    print("=" * 70, flush=True)
    print("[RUN] Starting orchestrator...", flush=True)
    print("=" * 70, flush=True)

    try:
        orchestrator.run()

    except KeyboardInterrupt:
        print("\n[RUN] Interrupted by user.", flush=True)
        print("[RUN] Existing progress should remain in the work directory.", flush=True)
        raise

    except Exception as exc:
        print("=" * 70, flush=True)
        print("[ERROR] Orchestrator crashed!", flush=True)
        print(f"[ERROR] {type(exc).__name__}: {exc}", flush=True)
        print("=" * 70, flush=True)
        raise

    print("=" * 70, flush=True)
    print("[RUN] Orchestrator finished.", flush=True)
    print("=" * 70, flush=True)


if __name__ == "__main__":
    main()
