"""Remembers what the agent has said since the caller last spoke.

Sits right after the LLM. The tool handler reads it to stop a booking from
being made in the same breath as the read-back question (see tools.readback_unanswered).
"""

from __future__ import annotations

from pipecat.frames.frames import Frame, LLMTextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class AgentTurnText(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.text = ""
        self.last_caller = ""  # what the caller said in their latest turn

    def reset(self, caller_said: str = "") -> None:
        self.text = ""
        self.last_caller = caller_said

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            self.text += frame.text
        await self.push_frame(frame, direction)
