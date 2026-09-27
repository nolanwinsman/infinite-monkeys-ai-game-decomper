"""
Minimal client for a local Ollama instance. Deliberately has no concept of
API keys, billing, or rate limits - the whole point is this runs against
hardware you own, for as long as you let it, at zero marginal cost.

Point OLLAMA_HOST at the sidecar container's service name (see
docker/docker-compose.yml) rather than localhost when running inside the
agent container.
"""

from __future__ import annotations

import json
import os
import urllib.request


class OllamaClient:
    def __init__(self, model: str, host: str | None = None, timeout_s: int = 600):
        self.model = model
        self.host = host or os.environ.get("OLLAMA_HOST", "http://ollama:11434")
        self.timeout_s = timeout_s

    def generate(self, prompt: str, temperature: float = 0.2) -> str:
        payload = json.dumps({
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.host}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("response", "")
