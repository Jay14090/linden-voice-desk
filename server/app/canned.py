"""Pre-synthesised audio for fixed phrases: the greeting and the "let me check" fillers.

These never change, so they're rendered once with ElevenLabs at server start
and played straight into the call. That skips a TTS round trip (often 0.5 to
1.5 s from here) at the two moments a caller notices silence most: when the
call connects, and while a tool runs.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import urllib.request
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import Frame, TTSAudioRawFrame, TTSStoppedFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

SAMPLE_RATE = 24000  # Pipecat's default output rate
CHUNK = SAMPLE_RATE * 2 // 25  # 40 ms of 16-bit mono

_cache: dict[tuple[str, str, str], bytes] = {}
_DISK = Path(__file__).resolve().parent.parent / "data" / "phrases"  # survives restarts, saves TTS credits


def _disk_path(voice: str, model: str, text: str) -> Path:
    return _DISK / (hashlib.sha1(f"{voice}|{model}|{SAMPLE_RATE}|{text}".encode()).hexdigest()[:16] + ".pcm")


def _fetch(api_key: str, voice: str, model: str, text: str) -> bytes:
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=pcm_{SAMPLE_RATE}",
        data=json.dumps({"text": text, "model_id": model, "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}}).encode(),
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


async def warm(api_key: str, voice: str, model: str, phrases: list[str]) -> None:
    """Render any phrases not cached yet. Failures are logged; live TTS is the fallback."""
    for p in phrases:
        f = _disk_path(voice, model, p)
        if (voice, model, p) not in _cache and f.exists():
            _cache[(voice, model, p)] = f.read_bytes()
    missing = [p for p in phrases if (voice, model, p) not in _cache]

    # one at a time: the free tier allows very few concurrent requests (429 otherwise)
    for text in missing:
        for attempt in range(3):
            try:
                audio = await asyncio.to_thread(_fetch, api_key, voice, model, text)
                _cache[(voice, model, text)] = audio
                _DISK.mkdir(parents=True, exist_ok=True)
                _disk_path(voice, model, text).write_bytes(audio)
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    logger.warning(f"could not pre-render {text!r}: {e}")
                else:
                    await asyncio.sleep(1.0 + attempt)
    if missing:
        logger.info(f"pre-rendered {sum((voice, model, p) in _cache for p in missing)}/{len(missing)} phrases")


def get(voice: str, model: str, text: str) -> bytes | None:
    return _cache.get((voice, model, text))


class CannedSpeech(FrameProcessor):
    """Sits between TTS and the transport and can inject cached audio on demand."""

    def __init__(self, voice: str, model: str):
        super().__init__()
        self._voice = voice
        self._model = model

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)

    async def say(self, text: str) -> bool:
        """Play a cached phrase. Returns False if it isn't cached (caller should fall back to TTS)."""
        audio = get(self._voice, self._model, text)
        if not audio:
            return False
        for i in range(0, len(audio), CHUNK):
            await self.push_frame(TTSAudioRawFrame(audio=audio[i : i + CHUNK], sample_rate=SAMPLE_RATE, num_channels=1))
        # marks the end of speech right away; otherwise the transport only notices after an idle timeout
        await self.push_frame(TTSStoppedFrame())
        return True
