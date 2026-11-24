"""End-to-end voice test: a scripted caller phones the real pipeline over WebRTC.

The caller's lines are synthesised with an ElevenLabs voice, streamed into the
call as microphone audio, and spoken one at a time after the agent finishes
talking. Prints the live transcript, records the agent's audio, and checks
the booking landed.

    uv run python scripts/e2e_call.py            # server must be running on :7860
"""

from __future__ import annotations

import asyncio
import fractions
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import av
import numpy as np
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRecorder
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
ENV = dotenv_values(ROOT / ".env")
SERVER = os.getenv("SERVER", "http://localhost:7860")
CALLER_VOICE = "CwhRBWXzGAHq8TQ4Fs17"  # "Roger", a default voice
RATE = 48000
FRAME = 960  # 20 ms

LINES = [
    "Hi, I'd like to see a skin doctor, please.",
    "The first one works.",
    "Jay Shah. My number is nine eight seven six five, four three two one zero.",
    "Yes, that's right.",
    "No, that's all. Thank you, bye!",
]


def tts_pcm(text: str) -> np.ndarray:
    cache = ROOT / "data" / "e2e_cache"
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / f"{abs(hash((CALLER_VOICE, text))) % 10**12}.pcm"
    if not f.exists():
        req = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{CALLER_VOICE}?output_format=pcm_16000&model_id=eleven_flash_v2_5",
            data=json.dumps({"text": text}).encode(),
            headers={"xi-api-key": ENV["ELEVENLABS_API_KEY"], "Content-Type": "application/json"},
        )
        f.write_bytes(urllib.request.urlopen(req).read())
    pcm16k = np.frombuffer(f.read_bytes(), dtype=np.int16).astype(np.float32)
    x = np.arange(len(pcm16k) * 3) / 3
    return np.interp(x, np.arange(len(pcm16k)), pcm16k).astype(np.int16)


class CallerMic(MediaStreamTrack):
    kind = "audio"

    def __init__(self):
        super().__init__()
        self.queue = np.zeros(0, dtype=np.int16)
        self.pts = 0
        self.t0 = None

    def say(self, pcm: np.ndarray):
        self.queue = np.concatenate([self.queue, pcm, np.zeros(RATE // 2, dtype=np.int16)])

    @property
    def talking(self):
        return len(self.queue) > 0

    async def recv(self):
        if self.t0 is None:
            self.t0 = time.time()
        wait = self.t0 + self.pts / RATE - time.time()
        if wait > 0:
            await asyncio.sleep(wait)
        chunk, self.queue = self.queue[:FRAME], self.queue[FRAME:]
        if len(chunk) < FRAME:
            chunk = np.concatenate([chunk, np.zeros(FRAME - len(chunk), dtype=np.int16)])
        frame = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = RATE
        frame.pts = self.pts
        frame.time_base = fractions.Fraction(1, RATE)
        self.pts += FRAME
        return frame


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(SERVER + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


async def main():
    clips = [tts_pcm(l) for l in LINES]
    print(f"synthesised {len(clips)} caller lines")

    pc = RTCPeerConnection()
    mic = CallerMic()
    pc.addTrack(mic)
    out = ROOT / "data" / "e2e_agent.wav"
    recorder = MediaRecorder(str(out))
    dc = pc.createDataChannel("chat", ordered=True)

    bot_speaking = asyncio.Event()
    bot_quiet = asyncio.Event()
    ready = asyncio.Event()
    events: list[str] = []
    started = time.time()

    def stamp():
        return f"{time.time() - started:5.1f}s"

    @pc.on("track")
    def on_track(track):
        recorder.addTrack(track)

    @dc.on("open")
    def on_open():
        dc.send(json.dumps({"label": "rtvi-ai", "type": "client-ready", "id": "e2e", "data": {"version": "1.0.0", "about": {"library": "e2e-script"}}}))

    @dc.on("message")
    def on_message(raw):
        try:
            m = json.loads(raw)
        except Exception:
            return
        t, d = m.get("type"), m.get("data") or {}
        if t == "bot-ready":
            ready.set()
        elif t == "bot-started-speaking":
            bot_quiet.clear()
            bot_speaking.set()
        elif t == "bot-stopped-speaking":
            bot_speaking.clear()
            bot_quiet.set()
        elif t == "user-transcription" and d.get("final"):
            print(f"{stamp()}  \033[32mCALLER heard as\033[0m: {d.get('text')}")
        elif t == "bot-output" and (d.get("output_type") or d.get("aggregated_by")) == "sentence" and d.get("spoken_status", "new") == "new":
            print(f"{stamp()}  \033[36mAGENT\033[0m: {d.get('text')}")
        elif t == "llm-function-call-started" or t == "llm-function-call-in-progress":
            print(f"{stamp()}  \033[33mtool\033[0m {d.get('function_name')} {json.dumps(d.get('arguments') or {})}")
        elif t == "server-message":
            print(f"{stamp()}  \033[35mserver\033[0m {d.get('type')} {d.get('appointment', {}).get('id')}")
        events.append(t)

    async def keepalive():
        while True:
            await asyncio.sleep(1)
            if dc.readyState == "open":
                dc.send(f"ping: {time.time()}")

    await pc.setLocalDescription(await pc.createOffer())
    answer = post("/api/offer", {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})
    await pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))
    await recorder.start()
    ka = asyncio.create_task(keepalive())

    await asyncio.wait_for(ready.wait(), 20)
    print(f"{stamp()}  connected")

    async def wait_for_agent_turn(timeout=40):
        # the agent starts talking, then goes quiet for a moment
        await asyncio.wait_for(bot_speaking.wait(), timeout)
        while True:
            await asyncio.wait_for(bot_quiet.wait(), timeout)
            await asyncio.sleep(1.2)
            if bot_quiet.is_set():
                return

    await wait_for_agent_turn()
    for line, clip in zip(LINES, clips):
        print(f"{stamp()}  \033[32mCALLER says\033[0m: {line}")
        mic.say(clip)
        while mic.talking:
            await asyncio.sleep(0.1)
        try:
            await wait_for_agent_turn()
        except asyncio.TimeoutError:
            print(f"{stamp()}  (agent stayed silent)")

    await asyncio.sleep(1)
    ka.cancel()
    await recorder.stop()
    await pc.close()

    appts = json.load(urllib.request.urlopen(SERVER + "/api/appointments"))
    mine = [a for a in appts if a["phone"] == "9876543210"]
    print("\nappointments under 98765 43210:")
    for a in mine:
        print(f"  {a['id']} {a['status']} {a['doctor_name']} {a['day']} {a['start']} {a['patient_name']}")
    print(f"agent audio: {out} ({out.stat().st_size // 1024} KB)")
    sys.exit(0 if any(a["status"] == "booked" for a in mine) else 1)


if __name__ == "__main__":
    asyncio.run(main())
