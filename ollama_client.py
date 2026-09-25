"""
ollama_client.py — thin wrapper around the local Ollama HTTP API.

Assumes Ollama is already running (`ollama serve`, or the systemd
service) and the target model has already been pulled
(`ollama pull qwen2.5-coder:1.5b`).

NOT testable in this sandbox — there is no Ollama server here. This
has to be exercised on your machine as part of the wp_001 dry run.
"""

import time

import requests

OLLAMA_URL = "http://localhost:11434/api/generate"


def call_ollama(model: str, prompt: str, temperature: float = 0.0, seed: int = 42, timeout_s: int = 180) -> dict:
    """
    Returns {"response": str, "latency_ms": int}.
    Raises requests.RequestException on a connection/timeout failure —
    the caller decides how that should be logged.
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "seed": seed},
    }
    start = time.monotonic()
    resp = requests.post(OLLAMA_URL, json=payload, timeout=timeout_s)
    resp.raise_for_status()
    latency_ms = int((time.monotonic() - start) * 1000)
    data = resp.json()
    return {"response": data.get("response", ""), "latency_ms": latency_ms}
