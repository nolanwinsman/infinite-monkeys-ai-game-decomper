#!/usr/bin/env bash

GAME="/home/nw/games/emulators/nes/Super Mario Bros. 3 (USA) (Rev A).nes"
WORK="/home/nw/games/decomps/nes/sm3test"
TIME="1"

# Where to store logs
LOG_DIR="$WORK/logs"
mkdir -p "$LOG_DIR"

# Timestamp for this run
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
LOG_FILE="$LOG_DIR/run_$TIMESTAMP.log"

echo "Starting decomp-agent..."
echo "Log file: $LOG_FILE"

nohup python3 -u run.py \
	--console nes \
	--rom "$GAME" \
	--workdir "$WORK" \
	--model "qwen2.5-coder:14b" \
	--ollama-host "http://127.0.0.1:11434" \
	--time-limit-hours "$TIME" \
	>"$LOG_FILE" 2>&1 &

PID=$!

echo "Started in background."
echo "PID: $PID"
echo "Follow the log with:"
echo "  tail -f \"$LOG_FILE\""
echo
echo "Check if it's running with:"
echo "  ps -p $PID"
