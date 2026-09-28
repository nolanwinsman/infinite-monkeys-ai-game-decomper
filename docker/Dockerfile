# Sandboxed environment for the agent loop. This container should never be
# given real network access beyond talking to the ollama sidecar - see
# docker-compose.yml for the network isolation, and the run script's
# --network flag if you invoke this image directly instead.
FROM python:3.12-slim

# cc65 (ca65/ld65) for the NES plugin. Add other consoles' toolchains here
# as their plugins are built (e.g. a built SN GCC + wibo for a GameCube-era
# plugin, per re4's tools/sn-gcc/build.sh approach).
RUN apt-get update && apt-get install -y --no-install-recommends \
        cc65 \
        build-essential \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pytest

WORKDIR /app
COPY engine/ engine/
COPY consoles/ consoles/
COPY tools/ tools/
COPY run.py run.py
COPY tests/ tests/
COPY docker/entrypoint.sh docker/entrypoint.sh
RUN chmod +x docker/entrypoint.sh

# Everything the agent is allowed to touch lives under /work: the ROM
# (mounted read-only), the growing source tree, and the scoreboard. The
# container has no reason to write anywhere else.
VOLUME ["/work"]

# Run as a non-root, unprivileged user - defense in depth alongside the
# network isolation, in case a "creative" model output ever tries something
# outside its lane (e.g. shelling out from inside generated source, which
# this pipeline never executes as code anyway, only assembles/compiles).
RUN useradd -m agent
USER agent

ENTRYPOINT ["python3", "run.py"]
