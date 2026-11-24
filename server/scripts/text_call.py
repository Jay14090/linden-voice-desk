"""Run a scripted caller against the agent in text, no audio.

Same system prompt, tool schemas and tool code as the voice pipeline, so the
agent's behaviour can be checked without spending TTS/STT credits.

    uv run python scripts/text_call.py              # built-in booking + cancel script
    uv run python scripts/text_call.py -i           # type the caller's lines yourself
    uv run python scripts/text_call.py --script s.txt   # one caller line per row
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from openai import OpenAI

from app.bot import openai_model_kwargs
from app.prompt import GREETING, system_prompt
from app.store import Store
from app.tools import NOT_DONE, TOOLS, caller_said_goodbye, run_tool

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

SCRIPT = [
    "Hi, I've had a skin rash for a few days. Can I see a dermatologist?",
    "The first one works.",
    "Jay Shah.",
    "nine eight seven six five, four three two one zero",
    "Yes, that's right.",
    "Actually, can you cancel that appointment? Something came up.",
    "Yes, same number.",
    "Yes.",
    "No, that's all. Thanks!",
]


def openai_tools():
    return [
        {
            "type": "function",
            "function": {
                "name": fs.name,
                "description": fs.description,
                "parameters": {"type": "object", "properties": fs.properties, "required": fs.required},
            },
        }
        for fs in TOOLS.standard_tools
    ]


def main():
    args = sys.argv[1:]
    interactive = "-i" in args
    script = SCRIPT
    if "--script" in args:
        script = [l.strip() for l in Path(args[args.index("--script") + 1]).read_text(encoding="utf-8").splitlines() if l.strip()]

    store = Store(":memory:")
    store.seed()
    client = OpenAI()
    model = os.getenv("OPENAI_MODEL") or "gpt-4.1-mini"
    messages: list[dict] = [
        {"role": "system", "content": system_prompt(store)},
        {"role": "assistant", "content": GREETING},
    ]
    print(f"[36mAGENT[0m (fixed): {GREETING}")

    last_caller = {"text": ""}

    def agent_turn():
        while True:
            t = time.time()
            resp = client.chat.completions.create(model=model, messages=messages, tools=openai_tools(), max_completion_tokens=220, **openai_model_kwargs(model))
            msg = resp.choices[0].message
            messages.append(msg.model_dump(exclude_none=True))
            if msg.content:
                print(f"\033[36mAGENT\033[0m ({time.time()-t:.1f}s): {msg.content.strip()}")
            if not msg.tool_calls:
                return
            for call in msg.tool_calls:
                fargs = json.loads(call.function.arguments or "{}")
                if call.function.name == "end_call" and not caller_said_goodbye(last_caller["text"]):
                    result = dict(NOT_DONE)
                else:
                    result = run_tool(store, call.function.name, fargs, msg.content or "")
                result.pop("_record", None)
                print(f"\033[33m  tool\033[0m {call.function.name}({json.dumps(fargs)}) -> ok={result.get('ok')} {result.get('error') or ''}")
                messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)})

    lines = iter(script)
    while True:
        try:
            line = input("CALLER: ") if interactive else next(lines)
        except (StopIteration, EOFError):
            break
        if not interactive:
            print(f"\033[32mCALLER\033[0m: {line}")
        messages.append({"role": "user", "content": line})
        last_caller["text"] = line
        agent_turn()

    print("\nappointments made in this call:")
    for a in store.appointments():
        if a["source"] != "seed":
            print(f"  {a['id']} {a['status']:9} {a['doctor_name']} {a['day']} {a['start']} {a['phone']} {a['patient_name']}")


if __name__ == "__main__":
    main()
