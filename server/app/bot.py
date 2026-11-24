"""The voice pipeline for one call.

browser mic -> WebRTC -> Silero VAD -> ElevenLabs STT -> Gemini (tools) -> ElevenLabs TTS -> WebRTC -> browser
"""

from __future__ import annotations

import asyncio
import os
import time

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    AssistantTurnStoppedMessage,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
    UserTurnStoppedMessage,
)
from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.turns.user_mute import (
    FunctionCallUserMuteStrategy,
    MuteUntilFirstBotCompleteUserMuteStrategy,
)
from pipecat.workers.runner import WorkerRunner

from . import canned
from .events import bus
from .prompt import CLINIC_NAME, GREETING, system_prompt
from .store import Store
from .tools import TOOLS, register_tools
from .turn_text import AgentTurnText

FILLERS = {
    "check_doctor_availability": "Let me check.",
    "book_appointment": "Booking that now.",
    "cancel_appointment": "Cancelling that now.",
}

DEFAULT_VOICE = "hpp4J3VqNfWAUOO0d1Us"  # "Bella", an ElevenLabs default voice usable on the free tier


def openai_model_kwargs(model: str) -> dict:
    """Request options per model family. GPT-5.x reasoning models are fastest with reasoning off."""
    if model.startswith("gpt-5"):
        effort = "minimal" if model.startswith("gpt-5-") else "none"
        return {"reasoning_effort": effort}
    return {"temperature": 0.4}


def build_llm(instruction: str):
    """Gemini by default; OpenAI if LLM_PROVIDER=openai (handy when the Gemini quota runs out)."""
    provider = os.getenv("LLM_PROVIDER", "google").lower()
    if provider == "openai":
        from pipecat.services.openai.llm import OpenAILLMService

        model = os.getenv("OPENAI_MODEL") or "gpt-5.4-mini"
        opts = openai_model_kwargs(model)
        settings = OpenAILLMService.Settings(model=model, system_instruction=instruction, extra={k: v for k, v in opts.items() if k != "temperature"})
        if "temperature" in opts:
            settings.temperature = opts["temperature"]
        return OpenAILLMService(api_key=os.environ["OPENAI_API_KEY"], settings=settings)

    from pipecat.services.google.llm import GoogleLLMService

    settings = {"system_instruction": instruction, "temperature": 0.4}
    if os.getenv("GOOGLE_MODEL"):
        settings["model"] = os.environ["GOOGLE_MODEL"]
    return GoogleLLMService(api_key=os.environ["GOOGLE_API_KEY"], settings=GoogleLLMService.Settings(**settings))


def tts_voice_and_model() -> tuple[str, str]:
    return (os.getenv("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE, os.getenv("ELEVENLABS_TTS_MODEL") or "eleven_flash_v2_5")


async def warm_phrases() -> None:
    """Pre-render the greeting and fillers; called once at server start."""
    if not os.getenv("ELEVENLABS_API_KEY"):
        return
    voice, model = tts_voice_and_model()
    await canned.warm(os.environ["ELEVENLABS_API_KEY"], voice, model, [GREETING, *FILLERS.values(), "One moment."])


async def run_call(connection: SmallWebRTCConnection, store: Store, session_id: str) -> None:
    logger.info(f"[{session_id}] call starting")
    started = time.time()

    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(audio_in_enabled=True, audio_out_enabled=True),
    )

    stt = ElevenLabsRealtimeSTTService(
        api_key=os.environ["ELEVENLABS_API_KEY"],
        settings=ElevenLabsRealtimeSTTService.Settings(
            language="en",
            # bias recognition toward the names callers will say
            keyterms=[d["name"].replace("Dr. ", "") for d in store.doctors()] + [CLINIC_NAME],
        ),
    )

    voice, tts_model = tts_voice_and_model()
    tts = ElevenLabsTTSService(
        api_key=os.environ["ELEVENLABS_API_KEY"],
        settings=ElevenLabsTTSService.Settings(
            voice=voice,
            model=tts_model,
            stability=0.5,
            similarity_boost=0.75,
        ),
        # ElevenLabs is occasionally slow to start a sentence; don't drop it too early
        stop_frame_timeout_s=8.0,
    )

    canned_speech = canned.CannedSpeech(voice, tts_model)
    llm = build_llm(system_prompt(store))
    turn_text = AgentTurnText()
    register_tools(llm, store, session_id, turn_text)

    context = LLMContext(tools=TOOLS)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            # Background noise shouldn't cut off the greeting, and the caller
            # talking over a tool call shouldn't cancel it half way.
            user_mute_strategies=[
                MuteUntilFirstBotCompleteUserMuteStrategy(),
                FunctionCallUserMuteStrategy(),
            ],
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            turn_text,
            tts,
            canned_speech,
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
    )
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)

    @worker.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        bus.publish("call", {"session": session_id, "state": "connected"})
        # The greeting is pre-rendered audio, so it plays the moment the call connects:
        # no LLM call and no TTS round trip. Falls back to live TTS if it isn't cached.
        if await canned_speech.say(GREETING):
            context.add_message({"role": "assistant", "content": GREETING})
            await rtvi.send_server_message({"type": "agent.line", "text": GREETING})
        else:
            await worker.queue_frames([TTSSpeakFrame(GREETING)])  # adds itself to the context

    @llm.event_handler("on_function_calls_started")
    async def on_function_calls_started(service, function_calls):
        # Fill the silence while the tool runs and the LLM writes its answer,
        # unless the agent already said something this turn.
        if turn_text.text.strip():
            return
        name = function_calls[0].function_name if function_calls else ""
        filler = FILLERS.get(name, "One moment.")

        async def play_filler():
            # Start a beat later so the tool result reaches the context ahead of the
            # filler audio: the LLM then writes the answer while the filler plays,
            # instead of waiting for it to finish.
            await asyncio.sleep(0.15)
            if turn_text.text.strip():
                return
            if not await canned_speech.say(filler):
                await service.push_frame(TTSSpeakFrame(filler, append_to_context=False))

        asyncio.create_task(play_filler())

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info(f"[{session_id}] caller hung up")
        await runner.cancel()

    @user_aggregator.event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(aggregator, strategy, message: UserTurnStoppedMessage):
        turn_text.reset()  # the caller answered; anything the agent says next is a new turn
        logger.info(f"[{session_id}] caller: {message.content}")

    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(aggregator, message: AssistantTurnStoppedMessage):
        logger.info(f"[{session_id}] agent: {message.content}")

    try:
        await runner.run()
    finally:
        bus.publish("call", {"session": session_id, "state": "ended", "seconds": round(time.time() - started)})
        logger.info(f"[{session_id}] call ended after {time.time() - started:.0f}s")
