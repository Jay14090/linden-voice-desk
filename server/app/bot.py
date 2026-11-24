"""The voice pipeline for one call.

browser mic -> WebRTC -> Silero VAD -> ElevenLabs STT -> Gemini (tools) -> ElevenLabs TTS -> WebRTC -> browser
"""

from __future__ import annotations

import os
import time

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
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

from .events import bus
from .prompt import CLINIC_NAME, system_prompt
from .store import Store
from .tools import TOOLS, register_tools
from .turn_text import AgentTurnText

DEFAULT_VOICE = "hpp4J3VqNfWAUOO0d1Us"  # "Bella", an ElevenLabs default voice usable on the free tier


def build_llm(instruction: str):
    """Gemini by default; OpenAI if LLM_PROVIDER=openai (handy when the Gemini quota runs out)."""
    provider = os.getenv("LLM_PROVIDER", "google").lower()
    if provider == "openai":
        from pipecat.services.openai.llm import OpenAILLMService

        return OpenAILLMService(
            api_key=os.environ["OPENAI_API_KEY"],
            settings=OpenAILLMService.Settings(model=os.getenv("OPENAI_MODEL") or "gpt-4.1-mini", system_instruction=instruction),
        )

    from pipecat.services.google.llm import GoogleLLMService

    settings = {"system_instruction": instruction, "temperature": 0.4}
    if os.getenv("GOOGLE_MODEL"):
        settings["model"] = os.environ["GOOGLE_MODEL"]
    return GoogleLLMService(api_key=os.environ["GOOGLE_API_KEY"], settings=GoogleLLMService.Settings(**settings))


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

    tts = ElevenLabsTTSService(
        api_key=os.environ["ELEVENLABS_API_KEY"],
        settings=ElevenLabsTTSService.Settings(
            voice=os.getenv("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE,
            model=os.getenv("ELEVENLABS_TTS_MODEL") or "eleven_flash_v2_5",
            stability=0.5,
            similarity_boost=0.75,
        ),
        # ElevenLabs is occasionally slow to start a sentence; don't drop it too early
        stop_frame_timeout_s=8.0,
    )

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
        context.add_message({"role": "developer", "content": "The call just connected. Greet the caller."})
        await worker.queue_frames([LLMRunFrame()])

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
