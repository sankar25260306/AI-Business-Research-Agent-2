"""
core/llm_client.py
Thin wrapper around a LOCALLY hosted LLM (Ollama by default).
No cloud/paid LLM API is used anywhere in this project.

Fixes vs. the original version:
  - request_timeout is now realistic for a 3B model doing JSON extraction on
    CPU (see core/config.py) instead of the old 20s, which meant nearly
    every call failed.
  - Sends keep_alive so the model isn't unloaded/reloaded between calls,
    which otherwise adds large cold-start latency on top of generation time.
  - Truncates oversized prompts (raw HTML pages can be 50k+ chars, which
    both slows CPU generation dramatically and risks silently exceeding
    the model's context window).
  - Retries on failure instead of giving up and silently dropping the whole
    business profile after a single timeout.

NOTE: This client stays *synchronous* on purpose (ollama.Client, not
AsyncClient). It's called from pipeline.py via `asyncio.to_thread(...)`
so it doesn't block the event loop, without risking a nested
`asyncio.run()` call inside code that's already running inside FastAPI's
event loop (that combination raises "asyncio.run() cannot be called from
a running event loop").
"""

import json
import re
import time
from typing import Optional

import ollama
from loguru import logger

from core.config import LLM


def _clean_and_truncate(text: str, max_chars: int) -> str:
    """Strip script/style noise and cap length before sending to the LLM."""
    if not text:
        return text
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars]
    return text


class LocalLLM:
    def __init__(self):
        self.client = ollama.Client(host=LLM.base_url, timeout=LLM.request_timeout)
        self.model = LLM.model

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        user = _clean_and_truncate(user, LLM.max_input_chars)
        last_error = None
        for attempt in range(1, LLM.max_retries + 2):  # e.g. max_retries=2 -> 3 attempts total
            try:
                response = self.client.chat(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    format="json" if json_mode else None,
                    keep_alive=LLM.keep_alive,
                    options={"temperature": LLM.temperature},
                )
                return response["message"]["content"]
            except Exception as e:  # noqa: BLE001 - catch/log/retry any ollama/network error
                last_error = e
                logger.warning(
                    f"Local LLM call attempt {attempt}/{LLM.max_retries + 1} failed "
                    f"({LLM.base_url}, model={self.model}): {e}"
                )
                if attempt <= LLM.max_retries:
                    time.sleep(1.5 * attempt)  # small backoff before retrying
        logger.error(f"Local LLM call failed after retries ({LLM.base_url}, model={self.model}): {last_error}")
        return ""

    def chat_json(self, system: str, user: str) -> Optional[dict]:
        """Ask the model for strict JSON and parse it defensively."""
        raw = self.chat(system, user, json_mode=True)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # fallback: pull the first {...} block out of the text
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    logger.warning("LLM returned unparsable JSON even after fallback extraction.")
            return None