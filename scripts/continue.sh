#!/usr/bin/env bash
# Resume decomping a game past a previous run's time limit. Safe to call
# repeatedly - the scoreboard persists between runs, so this only re-attempts
# units that were never matched (or were "stuck", if a pass through every
# other pending unit finds nothing left to do and time remains).
#
# Usage:
#   ./scripts/continue.sh --name smb3 [--hours 24] [--model qwen2.5-coder:14b]
#
# --hours defaults to whatever TIME_LIMIT_HOURS is already set to in
# docker/.env if not given.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${REPO_ROOT}/docker/.env"
COMPOSE="docker compose --env-file ${ENV_FILE} -f ${REPO_ROOT}/docker/docker-compose.yml"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "No docker/.env found - run scripts/setup.sh first." >&2
    exit 1
fi

NAME=""
HOURS=""
MODEL=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name) NAME="$2"; shift 2 ;;
        --hours) HOURS="$2"; shift 2 ;;
        --model) MODEL="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$NAME" ]]; then
    echo "Usage: $0 --name <game-slug> [--hours N] [--model NAME]" >&2
    exit 1
fi

GAME_DIR="${REPO_ROOT}/workdir/${NAME}"
if [[ ! -d "${GAME_DIR}/project" ]]; then
    echo "No existing project found at ${GAME_DIR}/project - run scripts/setup.sh first." >&2
    exit 1
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
[[ -n "$MODEL" ]] && set_env_var OLLAMA_MODEL "$MODEL"
[[ -n "$HOURS" ]] && set_env_var TIME_LIMIT_HOURS "$HOURS"

# shellcheck disable=SC1090
source "$ENV_FILE"

echo "Resuming ${NAME} for ${TIME_LIMIT_HOURS:-24} more hour(s) with model ${OLLAMA_MODEL:-qwen2.5-coder:7b}..."

# Make sure services are up (a machine reboot since the last run would have
# stopped them); this is a no-op if they're already running.
$COMPOSE up -d

$COMPOSE exec -d agent /app/docker/entrypoint.sh

echo
echo "Resumed. Progress so far:"
$COMPOSE exec -T agent python3 -c "
import json
try:
    with open('/work/${NAME}/project/scoreboard.json') as f:
        d = json.load(f)
    total = len(d['units'])
    matched = sum(1 for u in d['units'].values() if u['status'] == 'matched')
    stuck = sum(1 for u in d['units'].values() if u['status'] == 'stuck')
    print(f'{matched}/{total} units matched, {stuck} stuck')
except FileNotFoundError:
    print('No scoreboard yet - the run may still be starting up.')
"
echo
echo "Watch it live with:"
echo "  tail -f ${GAME_DIR}/logs/run-*.log"
