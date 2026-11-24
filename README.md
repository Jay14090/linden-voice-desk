# Linden: voice front desk for a clinic

A caller phones the clinic from the browser and talks to **Maya**, a voice agent who can check a doctor's free slots, book an appointment and cancel one. The caller is identified only by the phone number they say on the call, and the agent reads the number back before booking or cancelling. A live dashboard shows the call, the transcript, every tool call, and the schedule and appointment list changing as the agent works.

**LLM used:** OpenAI `gpt-5.4-mini` (reasoning off) in the demo. Google Gemini (`gemini-3.1-flash-lite`) also works by setting `LLM_PROVIDER=google`; I moved the demo to OpenAI because the Gemini free tier was returning 503 "high demand" errors and 2–10 s responses during testing, which is too slow for a phone call.

## Pipeline

```
 Browser mic ──WebRTC──▶ Silero VAD ─▶ ElevenLabs STT ─▶ user turn (Smart Turn v3)
                                       (Scribe v2 realtime)        │
                                                                   ▼
 Browser speaker ◀─WebRTC── ElevenLabs TTS ◀── read-back guard ◀── LLM + 3 tools
                           (Flash v2.5)                            │
                                                                   ▼
                                                     SQLite: doctors, slots, appointments
                                                                   │
                                     Dashboard ◀── SSE events ─────┘
```

- **Transport:** Pipecat `SmallWebRTCTransport`, a direct browser↔server WebRTC connection, with no third-party room service.
- **Turn taking:** Silero VAD plus Pipecat's Smart Turn v3 model decide when the caller has finished. The caller can't interrupt the greeting (so room noise doesn't cut it off), and talking during a tool call doesn't cancel the call.
- **Tools** (registered on the Pipecat LLM service, `server/app/tools.py`):
  | Tool | Does |
  |---|---|
  | `check_doctor_availability` | Free slots for a doctor id, a specialty or a need ("skin", "heart", "child"), optionally for one date. Returns at most three times per day so the agent never reads a long list. |
  | `book_appointment` | Books a slot for the caller's spoken number. Rejects it unless `phone_confirmed` is true, the number is 10 digits and the slot is still free; on a clash it returns the other free times that day. |
  | `cancel_appointment` | Finds the caller's upcoming appointments by number. If there's more than one, it asks which (by doctor and/or date). |
  | `end_call` | Hangs up once the caller is done. The goodbye (the agent's own, or a cached one if it didn't say any) plays first, then the pipeline closes. |
- **Problem first:** Maya opens with "What seems to be the problem today?" and maps the answer to a doctor using a list of what each specialty sees (a child always goes to the pediatrician; anything unclear goes to the general physician). She says who and why, and offers times in the same reply.
- **Phone read-back done in code:** the LLM writes the number as digits ("91827 36450"), which it copies reliably, and a TTS text transform (`server/app/speech.py`) speaks it digit by digit. When the model wrote the words itself it once dropped the leading digit.
- **Guardrails:** Maya only handles appointments. Maths, coding, general knowledge, medical advice, "ignore your instructions" and the like get one polite line and a steer back to booking (tested in `scripts/text_call.py`). Replies are capped at 220 tokens. `end_call` is refused in code unless the caller actually said goodbye, so an "okay" doesn't hang up on them.
- **Out of scope:** clinic hours are in the prompt. For anything else (address, fees, reports) Maya points the caller to the website (`CLINIC_WEBSITE`).
- **Read-back guard (`server/app/turn_text.py`):** in testing the model sometimes said "…4 3 2 1 0. Is that right?" and booked in the same reply, before the caller answered. A small processor after the LLM records what the agent has said since the caller last spoke. If that text holds the number and a question, the tool refuses and tells the model to wait. So the confirmation rule is enforced in code, not only in the prompt.
- **Spoken numbers:** callers say "nine double eight…", and STT returns words. `normalize_phone` turns words, "double"/"triple", `+91` and spaces into 10 digits.

## Latency

Measured with `scripts/e2e_call.py`: the time from when the caller stops speaking to when the agent's first audio plays (full booking call, same scripted caller, same network).

| | before | after |
|---|---|---|
| greeting after connect | ~1.5 s (LLM + TTS) | **instant** (pre-rendered) |
| turns that call a tool, first audio | 3.2–3.4 s | **1.6–1.7 s** |
| median, all turns | 2.46 s | **1.80 s** |
| worst turn | 3.43 s | **2.55 s** |

What made the difference:
- **Pre-rendered audio.** The greeting and short fillers ("Let me check.", "Booking that now.") are rendered once and cached on disk, then played straight into the call with no TTS round trip.
- **Fillers that don't delay the answer.** When the LLM calls a tool, a cached filler plays (unless the agent already spoke this turn). It starts 150 ms after the call, so the tool result reaches the context first and the LLM writes the answer *while* the filler plays. Pipecat holds the follow-up LLM call until the bot stops speaking, so a filler sent first would otherwise add its own length to the wait (it did: 5–6 s before this fix).
- **Clean end of speech.** Cached audio ends with a `TTSStoppedFrame`, so the transport knows straight away that the bot stopped, instead of waiting for an idle timeout.
- **Model choice.** `scripts/llm_latency.py` measures time to first token with the real prompt and tools. `gpt-5.4-mini` (reasoning off) is close to the fastest and followed every rule in the test scripts. `gpt-4.1-mini` is about 0.1 s faster but once mis-grouped the phone read-back; `gpt-5.4-nano` slipped on a multi-booking script. OpenAI's priority tier made no reliable difference from here. Any of them can be set with `OPENAI_MODEL`.
- **Short first sentence.** Replies open with a few words ("Sure.", "Got it."), so TTS starts on the first sentence while the rest is still being written.

What's left is mostly network round trips from India: STT finalising (~0.5 s), LLM first token (~0.8 s) and TTS first audio (~0.5 s).

## Dashboard

React + Vite, plain CSS, no UI kit. Two views, switched in the header.

**Front desk**
- **Try call** opens a phone on the side of the dashboard. It works like a normal call screen: it rings (an Indian ringback tone, synthesised with Web Audio), connects, and shows the timer, an avatar that pulses with whoever is speaking (ivory for Maya, mint for the caller), mute / speaker / end buttons, and a phone-style notification when something gets booked or cancelled. Nothing is transcribed on the phone itself.
- **Transcript:** the live conversation, shown on the dashboard next to the agent trace.
- **Schedule:** a timeline per doctor for each of the next seven days. Past time is hatched and a red line marks now. When the agent checks a doctor, their row lights up; when it books, the slot pops amber.
- **Appointments:** a list grouped by day; new bookings slide in highlighted, cancellations are struck through.
- **Agent trace:** each tool call with its arguments, result and timing, as it happens.

**Bookings** (the clinic's admin view)
- Filter by doctor (with upcoming counts) and by status (upcoming, past, cancelled).
- Search by patient name, phone number or problem.
- Every booking shows the patient's problem, captured by the agent during the call (`reason` on `book_appointment`).
- Click a booking to edit the name, number or problem, move it to another doctor, day or free time, or cancel or restore it. **New booking** adds a walk-in or a phoned-in booking by hand. All edits go through the same rules as the agent (10-digit numbers, free future slots only) and update every open screen live. Booked times show in the picker but can't be chosen; if a slot gets taken while you're editing, the form says so instead of quietly switching; a cancelled booking has to be restored before it can move; and the server re-checks everything, returning a plain-English error for any bad input.

Mobile: the phone goes full screen and the board scrolls sideways.

## Run it

Requirements: Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 20+, a microphone, and Chrome or Edge.

```bash
# 1. server
cd server
cp .env.example .env        # add ELEVENLABS_API_KEY and OPENAI_API_KEY (or GOOGLE_API_KEY + LLM_PROVIDER=google)
uv sync
uv run uvicorn app.main:app --port 7860

# 2. dashboard (second terminal)
cd web
npm install
npm run dev                 # http://localhost:5173
```

Open http://localhost:5173, click **Try call** and allow the microphone. Try: *"I need a skin doctor tomorrow."* The ↻ button in the header resets the demo data.

For a single process, run `npm run build` in `web/`; the server then serves the built dashboard at http://localhost:7860.

The ElevenLabs free tier works. It only allows its default voices over the API, so the default voice is "Bella".

## Tests

```bash
cd server
uv run pytest -q                        # 88 tests: booking rules, phone parsing, read-back guard, API input validation
uv run python scripts/text_call.py      # scripted conversation against the real prompt + tools, text only (no audio credits)
uv run python scripts/e2e_call.py       # full voice test: a synthetic caller phones the running server over WebRTC; prints response gaps
uv run python scripts/llm_latency.py gpt-5.4-mini gpt-4.1-mini   # time to first token per model, with the real prompt and tools
```

`e2e_call.py` synthesises the caller's lines with a second ElevenLabs voice, streams them into the call as microphone audio, takes turns using the agent's RTVI events, then checks the booking landed.

## Layout

```
server/app/
  bot.py        the Pipecat pipeline for one call
  tools.py      the three tools, their schemas, and the Pipecat handler
  prompt.py     system prompt (rebuilt per call with today's date and the doctor roster)
  store.py      SQLite: doctors, slots for the next 7 clinic days, appointments
  turn_text.py  remembers the agent's words this turn, for the read-back guard
  canned.py     pre-rendered greeting, filler and goodbye audio
  speech.py     speaks phone numbers digit by digit
  main.py       FastAPI: WebRTC signalling, REST for the dashboard (incl. edit/create bookings), SSE events
web/src/
  lib/useCall.ts   Pipecat client, call states, transcript, audio analysers
  components/      Schedule, Ledger, Activity, LiveTranscript, Phone, Bookings
```

Doctors, specialties and slots are mock data, regenerated relative to today so the demo never goes stale.
