#!/usr/bin/env bash
# One-time setup for a new game. Creates the output folder structure, copies
# your ROM in, builds the container image LOCALLY (no registry push), starts
# the ollama + agent services, pulls the configured model, and kicks off the
# first run in the background against the local model with continuous
# verify-and-retry per unit.
#
# Usage:
#   ./scripts/setup.sh --rom /path/to/smb3.nes --console nes --name smb3 \
#       [--model qwen2.5-coder:14b] [--hours 24]
#
# After this, watch progress with:
#   tail -f workdir/smb3/logs/run-*.log
#   cat workdir/smb3/project/scoreboard.json
#
# If it hits the time limit before finishing, keep going with:
#   ./scripts/continue.sh --name smb3 --hours 24
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="docker compose --env-file ${REPO_ROOT}/docker/.env -f ${REPO_ROOT}/docker/docker-compose.yml"

ROM=""
CONSOLE=""
NAME=""
MODEL=""
HOURS=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --rom) ROM="$2"; shift 2 ;;
        --console) CONSOLE="$2"; shift 2 ;;
        --name) NAME="$2"; shift 2 ;;
        --model) MODEL="$2"; shift 2 ;;
        --hours) HOURS="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$ROM" || -z "$CONSOLE" || -z "$NAME" ]]; then
    echo "Usage: $0 --rom <path> --console <nes|...> --name <game-slug> [--model NAME] [--hours N]" >&2
    exit 1
fi
if [[ ! -f "$ROM" ]]; then
    echo "ROM not found: $ROM" >&2
    exit 1
fi

GAME_DIR="${REPO_ROOT}/workdir/${NAME}"
if [[ -d "$GAME_DIR" ]]; then
    echo "Warning: ${GAME_DIR} already exists. Existing progress (scoreboard.json)" >&2
    echo "will be resumed, not overwritten. Use scripts/continue.sh instead if that's" >&2
    echo "what you want, or remove ${GAME_DIR} first for a truly fresh start." >&2
fi

mkdir -p "${GAME_DIR}/roms" "${GAME_DIR}/logs" "${GAME_DIR}/project"
ROM_FILENAME="$(basename "$ROM")"
cp -n "$ROM" "${GAME_DIR}/roms/${ROM_FILENAME}" || true

ROM_SHA1="$(sha1sum "$ROM" | cut -d' ' -f1)"
echo "ROM copied to ${GAME_DIR}/roms/${ROM_FILENAME}"
echo "SHA1: ${ROM_SHA1}  (cross-check this against a preservation database yourself)"

# Write/refresh docker/.env. Start from .env.example if no .env exists yet so
# hardware-tier model comments are preserved for future editing; otherwise
# only touch the values this command was actually given.
ENV_FILE="${REPO_ROOT}/docker/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    cp "${REPO_ROOT}/docker/.env.example" "$ENV_FILE"
fi

set_env_var() {
    local key="$1" val="$2"
    if grep -q "^${key}=" "$ENV_FILE"; then
        sed -i.bak "s|^${key}=.*|${key}=${val}|" "$ENV_FILE" && rm -f "${ENV_FILE}.bak"
    else
        echo "${key}=${val}" >> "$ENV_FILE"
    fi
}

set_env_var GAME_NAME "$NAME"
set_env_var CONSOLE "$CONSOLE"
set_env_var ROM_FILENAME "$ROM_FILENAME"
[[ -n "$MODEL" ]] && set_env_var OLLAMA_MODEL "$MODEL"
[[ -n "$HOURS" ]] && set_env_var TIME_LIMIT_HOURS "$HOURS"

# shellcheck disable=SC1090
source "$ENV_FILE"
echo "Using model: ${OLLAMA_MODEL:-qwen2.5-coder:7b}"
echo "Time budget for this run: ${TIME_LIMIT_HOURS:-24} hour(s)"

echo "Building agent image locally (no registry push)..."
$COMPOSE build agent

echo "Starting services..."
$COMPOSE up -d

echo "Waiting for ollama to be ready..."
for i in $(seq 1 30); do
    if $COMPOSE exec -T ollama ollama list >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

echo "Pulling model ${OLLAMA_MODEL:-qwen2.5-coder:7b} (one-time, then cached in the ollama_models volume)..."
$COMPOSE exec -T ollama ollama pull "${OLLAMA_MODEL:-qwen2.5-coder:7b}"

echo "Starting the decomp loop in the background..."
$COMPOSE exec -d agent /app/docker/entrypoint.sh

echo
echo "Started. This will keep verifying every unit against your ROM and"
echo "retrying with the local model until each matches, is marked stuck, or"
echo "the time budget runs out."
echo
echo "Watch progress:"
echo "  cat ${GAME_DIR}/project/scoreboard.json"
echo "  tail -f ${GAME_DIR}/logs/run-*.log"
echo
echo "If it hits the time limit before finishing:"
echo "  ./scripts/continue.sh --name ${NAME} --hours ${HOURS:-24}"
