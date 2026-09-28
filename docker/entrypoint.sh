cat > docker/entrypoint.sh << 'ENTRYPOINT_EOF'
#!/usr/bin/env bash
# Runs inside the agent container. Reads configuration from environment
# variables (set via docker/.env, see docker/.env.example) and invokes
# run.py. Both scripts/setup.sh and scripts/continue.sh call this the same
# way - the only difference between "start" and "continue" is that continue
# passes a possibly-different TIME_LIMIT_HOURS and runs against a workdir
# that already has a scoreboard.json (which run.py resumes automatically).
set -euo pipefail

: "${GAME_NAME:?GAME_NAME must be set}"
: "${CONSOLE:?CONSOLE must be set}"
: "${ROM_FILENAME:?ROM_FILENAME must be set}"
: "${OLLAMA_MODEL:=qwen2.5-coder:7b}"
: "${TIME_LIMIT_HOURS:=24}"
: "${MAX_ATTEMPTS_PER_UNIT:=8}"

GAME_DIR="/work/${GAME_NAME}"
ROM_PATH="${GAME_DIR}/roms/${ROM_FILENAME}"
PROJECT_DIR="${GAME_DIR}/project"
LOG_DIR="${GAME_DIR}/logs"

if [ ! -f "${ROM_PATH}" ]; then
    echo "ROM not found at ${ROM_PATH}" >&2
    echo "Looked for GAME_NAME=${GAME_NAME}, ROM_FILENAME=${ROM_FILENAME}" >&2
    echo "Available game folders under /work:" >&2
    ls -1 /work 2>/dev/null >&2
    exit 1
fi

mkdir -p "${LOG_DIR}"

LOG_FILE="${LOG_DIR}/run-$(date +%Y%m%dT%H%M%S).log"
echo "Starting run: game=${GAME_NAME} console=${CONSOLE} model=${OLLAMA_MODEL} hours=${TIME_LIMIT_HOURS}"
echo "Logging to ${LOG_FILE} (also see ${PROJECT_DIR}/scoreboard.json for live progress)"

python3 /app/run.py \
    --console "${CONSOLE}" \
    --rom "${ROM_PATH}" \
    --workdir "${PROJECT_DIR}" \
    --model "${OLLAMA_MODEL}" \
    --time-limit-hours "${TIME_LIMIT_HOURS}" \
    --max-attempts-per-unit "${MAX_ATTEMPTS_PER_UNIT}" \
    --verbose 2>&1 | tee "${LOG_FILE}"
ENTRYPOINT_EOF
chmod +x docker/entrypoint.sh
