"""HTTP side: WebRTC signalling for calls, REST + SSE for the dashboard."""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pipecat.transports.smallwebrtc.connection import IceServer, SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    IceCandidate,
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)

from .bot import run_call
from .events import bus
from .prompt import AGENT_NAME, CLINIC_NAME
from .store import Store, today

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=True)

DB_PATH = Path(os.getenv("DB_PATH") or ROOT / "data" / "clinic.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
store = Store(DB_PATH)

ice_servers = [IceServer(urls=u.strip()) for u in os.getenv("ICE_SERVERS", "").split(",") if u.strip()]
webrtc = SmallWebRTCRequestHandler(ice_servers=ice_servers or None)
calls: dict[str, asyncio.Task] = {}


def ensure_fresh_data() -> None:
    """Seed on first run, and re-seed once the slot window has drifted into the past."""
    slots = store.slots()
    horizon = (today() + timedelta(days=3)).isoformat()
    if not store.doctors() or not slots or slots[-1]["day"] < horizon:
        logger.info("seeding clinic data")
        store.seed()


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_fresh_data()
    missing = [k for k in ("ELEVENLABS_API_KEY", "GOOGLE_API_KEY") if not os.getenv(k)]
    if missing and os.getenv("LLM_PROVIDER", "google") == "google":
        logger.warning(f"missing env: {', '.join(missing)} - calls will fail until set in server/.env")
    yield
    for task in calls.values():
        task.cancel()
    await webrtc.close()


app = FastAPI(title=f"{CLINIC_NAME} voice desk", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- calls (WebRTC signalling) ----------

@app.post("/api/offer")
async def offer(request: Request):
    body = await request.json()
    req = SmallWebRTCRequest.from_dict(
        {k: body.get(k) for k in ("sdp", "type", "pc_id", "restart_pc", "request_data", "requestData") if k in body}
    )

    async def on_connection(connection: SmallWebRTCConnection):
        session_id = uuid.uuid4().hex[:8]
        bus.publish("call", {"session": session_id, "state": "connecting"})
        task = asyncio.create_task(run_call(connection, store, session_id))
        calls[session_id] = task
        task.add_done_callback(lambda _: calls.pop(session_id, None))

    return await webrtc.handle_web_request(request=req, webrtc_connection_callback=on_connection)


@app.patch("/api/offer")
async def ice_candidate(request: Request):
    body = await request.json()
    await webrtc.handle_patch_request(
        SmallWebRTCPatchRequest(
            pc_id=body["pc_id"],
            candidates=[IceCandidate(**c) for c in body.get("candidates", [])],
        )
    )
    return {"status": "ok"}


# ---------- dashboard data ----------

@app.get("/api/clinic")
def clinic():
    return {"name": CLINIC_NAME, "agent": AGENT_NAME, "today": today().isoformat(), "active_calls": len(calls)}


@app.get("/api/doctors")
def doctors():
    slots = store.slots(include_past=True)
    by_doctor: dict[str, list[dict]] = {}
    for s in slots:
        by_doctor.setdefault(s["doctor_id"], []).append({"day": s["day"], "start": s["start"], "free": s["free"], "past": s["past"]})
    return [{**d, "slots": by_doctor.get(d["id"], [])} for d in store.doctors()]


@app.get("/api/appointments")
def appointments():
    return store.appointments()


@app.post("/api/reset")
def reset():
    store.seed()
    bus.publish("reset", {})
    return {"ok": True}


@app.get("/api/events")
async def events(request: Request):
    queue = bus.subscribe()

    async def stream():
        try:
            yield "retry: 2000\n\n"
            while not await request.is_disconnected():
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/health")
def health():
    return JSONResponse(
        {
            "ok": True,
            "llm": os.getenv("LLM_PROVIDER", "google"),
            "keys": {k: bool(os.getenv(k)) for k in ("ELEVENLABS_API_KEY", "GOOGLE_API_KEY")},
        }
    )


# ---------- built frontend (production) ----------

WEB_DIST = ROOT.parent / "web" / "dist"
if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        file = WEB_DIST / path
        return FileResponse(file if path and file.is_file() else WEB_DIST / "index.html")
