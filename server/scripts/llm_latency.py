"""Measure time-to-first-token for candidate LLMs with the real prompt and tools.

    uv run python scripts/llm_latency.py gpt-4.1-mini gpt-4o-mini ...
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from openai import OpenAI

from app.prompt import system_prompt
from app.store import Store
from scripts.text_call import openai_tools

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

import os

TIER = os.getenv("TIER")  # e.g. TIER=priority

TURNS = [
    "Hi, I'd like to see a skin doctor tomorrow.",  # should call a tool
    "Great, and what's your name again?",  # plain reply
]


def ttft(client: OpenAI, model: str, messages: list, tools: list) -> tuple[float, str]:
    kwargs = {}
    if model.startswith("gpt-5"):
        kwargs["reasoning_effort"] = "none" if model.startswith(("gpt-5.1", "gpt-5.2", "gpt-5.4", "gpt-5.5", "gpt-5.6")) else "minimal"
    else:
        kwargs["temperature"] = 0.4
    if TIER:
        kwargs["service_tier"] = TIER
    t = time.perf_counter()
    stream = client.chat.completions.create(model=model, messages=messages, tools=tools, stream=True, **kwargs)
    first = None
    kind = ""
    for chunk in stream:
        if not chunk.choices:
            continue
        d = chunk.choices[0].delta
        if first is None and (d.content or d.tool_calls):
            first = time.perf_counter() - t
            kind = "tool" if d.tool_calls else "text"
    return first or (time.perf_counter() - t), kind


def main():
    models = sys.argv[1:] or ["gpt-4.1-mini", "gpt-4o-mini"]
    store = Store(":memory:")
    store.seed()
    client = OpenAI()
    tools = openai_tools()
    sys_msg = {"role": "system", "content": system_prompt(store)}
    for model in models:
        results = []
        try:
            for _ in range(int(os.getenv("ROUNDS", 3))):
                for turn in TURNS:
                    s, k = ttft(client, model, [sys_msg, {"role": "user", "content": turn}], tools)
                    results.append((s, k))
        except Exception as e:  # noqa: BLE001
            print(f"{model:16} error: {str(e)[:90]}")
            continue
        times = [r[0] for r in results[1:]]  # drop the cold first call
        print(f"{model:16} median {statistics.median(times):.2f}s  min {min(times):.2f}s  max {max(times):.2f}s  {[r[1] for r in results[:2]]}")


if __name__ == "__main__":
    main()
