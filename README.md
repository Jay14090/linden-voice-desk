# Linden: voice front desk for a clinic

A caller phones the clinic from the browser and talks to **Maya**, a voice agent who can check a doctor's free slots, book an appointment and cancel one. The caller is identified only by the phone number they say on the call, and the agent reads the number back before booking or cancelling. A live dashboard shows the call, the transcript, every tool call, and the schedule and appointment list changing as the agent works.

**LLM used:** OpenAI `gpt-4.1-mini` in the demo. Google Gemini (`gemini-3.1-flash-lite`) also works by setting `LLM_PROVIDER=google`; I moved the demo to OpenAI because the Gemini free tier was returning 503 "high demand" errors and 2–10 s responses during testing, which is too slow for a phone call.

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
- **Read-back guard (`server/app/turn_text.py`):** in testing the model sometimes said "…4 3 2 1 0. Is that right?" and booked in the same reply, before the caller answered. A small processor after the LLM records what the agent has said since the caller last spoke. If that text holds the number and a question, the tool refuses and tells the model to wait. So the confirmation rule is enforced in code, not only in the prompt.
- **Spoken numbers:** callers say "nine double eight…", and STT returns words. `normalize_phone` turns words, "double"/"triple", `+91` and spaces into 10 digits.

## Dashboard

React + Vite, plain CSS, no UI kit.

- **Try call** opens a phone on the side of the dashboard: it rings (an Indian ringback tone, synthesised with Web Audio), connects, then shows a call timer, a live waveform (agent in ivory, caller in mint), the transcript, mute / end / speaker buttons, and a ticket card inside the phone when something is booked or cancelled.
- **Schedule:** a timeline per doctor for each of the next seven days. Past time is hatched and a red line marks now. When the agent checks a doctor, their row lights up; when it books, the slot pops amber.
- **Appointments:** a list grouped by day; new bookings slide in highlighted, cancellations are struck through.
- **Agent trace:** each tool call with its arguments, result and timing, as it happens.
- Mobile: the phone goes full screen and the board scrolls sideways.

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
uv run pytest -q                        # 31 unit tests: slots, booking rules, phone parsing, the read-back guard
uv run python scripts/text_call.py      # scripted conversation against the real prompt + tools, text only (no audio credits)
uv run python scripts/e2e_call.py       # full voice test: a synthetic caller phones the running server over WebRTC
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
  main.py       FastAPI: WebRTC signalling, REST for the dashboard, SSE events
web/src/
  lib/useCall.ts   Pipecat client, call states, transcript, audio analysers
  components/      Schedule, Ledger, Activity, Phone, Waveform
```

Doctors, specialties and slots are mock data, regenerated relative to today so the demo never goes stale.
