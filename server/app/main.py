"""HTTP side: WebRTC signalling for calls, REST + SSE for the dashboard."""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
import re
from datetime import date as _date, time as _time
from typing import Literal

from pydantic import BaseModel, field_validator
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

from .bot import run_call, warm_phrases
from .events import bus
from .prompt import AGENT_NAME, CLINIC_NAME
from .store import Store, StoreError, normalize_phone, today

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
    await warm_phrases()
    missing = [k for k in ("ELEVENLABS_API_KEY", "GOOGLE_API_KEY") if not os.getenv(k)]
    if missing and os.getenv("LLM_PROVIDER", "google") == "google":
        logger.warning(f"missing env: {', '.join(missing)} - calls will fail until set in server/.env")
    yield
    for task in calls.values():
        task.cancel()
    await webrtc.close()


app = FastAPI(title=f"{CLINIC_NAME} voice desk", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def readable_validation_error(request: Request, exc: RequestValidationError):
    msgs = []
    for err in exc.errors():
        msg = str(err.get("msg", "Invalid input")).removeprefix("Value error, ")
        if err.get("type") == "literal_error":
            msg = "Status must be booked or cancelled."
        elif err.get("type") == "json_invalid":
            msg = "Request body isn't valid JSON."
        elif err.get("type") == "missing":
            msg = f"{err['loc'][-1].replace('_', ' ').capitalize()} is required."
        msgs.append(msg)
    return JSONResponse(status_code=422, content={"detail": " ".join(dict.fromkeys(msgs)) or "Invalid input."})
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


DOCTOR_IDS = {d["id"] for d in store.doctors()} or {"mehta", "rao", "iyer", "sheikh", "kulkarni", "das"}


def _check_name(v):
    if v is None:
        return v
    v = re.sub(r"\s+", " ", v).strip()
    if not v:
        raise ValueError("Patient name can't be empty.")
    if len(v) > 80:
        raise ValueError("Patient name is too long (80 characters max).")
    return v


def _check_reason(v):
    if v is None:
        return v
    v = v.strip()
    if len(v) > 300:
        raise ValueError("Problem is too long (300 characters max).")
    return v


def _check_phone(v):
    if v is None:
        return v
    digits = normalize_phone(v)
    if not digits:
        raise ValueError("Phone must be a 10-digit mobile number.")
    return digits


def _check_doctor(v):
    if v is not None and v not in DOCTOR_IDS:
        raise ValueError("Unknown doctor.")
    return v


def _check_day(v):
    if v is None:
        return v
    try:
        return _date.fromisoformat(v).isoformat()
    except (TypeError, ValueError):
        raise ValueError("Day must be a date like 2026-11-25.") from None


def _check_start(v):
    if v is None:
        return v
    if not re.fullmatch(r"\d{2}:\d{2}", v or ""):
        raise ValueError("Time must look like 10:30.")
    try:
        _time.fromisoformat(v)
    except ValueError:
        raise ValueError("Time must look like 10:30.") from None
    return v


class AppointmentPatch(BaseModel):
    patient_name: str | None = None
    phone: str | None = None
    reason: str | None = None
    doctor_id: str | None = None
    day: str | None = None
    start: str | None = None
    status: Literal["booked", "cancelled"] | None = None

    _name = field_validator("patient_name")(classmethod(lambda cls, v: _check_name(v)))
    _reason = field_validator("reason")(classmethod(lambda cls, v: _check_reason(v)))
    _phone = field_validator("phone")(classmethod(lambda cls, v: _check_phone(v)))
    _doctor = field_validator("doctor_id")(classmethod(lambda cls, v: _check_doctor(v)))
    _day = field_validator("day")(classmethod(lambda cls, v: _check_day(v)))
    _start = field_validator("start")(classmethod(lambda cls, v: _check_start(v)))


class AppointmentCreate(BaseModel):
    doctor_id: str
    day: str
    start: str
    phone: str
    patient_name: str
    reason: str | None = None

    _name = field_validator("patient_name")(classmethod(lambda cls, v: _check_name(v)))
    _reason = field_validator("reason")(classmethod(lambda cls, v: _check_reason(v)))
    _phone = field_validator("phone")(classmethod(lambda cls, v: _check_phone(v)))
    _doctor = field_validator("doctor_id")(classmethod(lambda cls, v: _check_doctor(v)))
    _day = field_validator("day")(classmethod(lambda cls, v: _check_day(v)))
    _start = field_validator("start")(classmethod(lambda cls, v: _check_start(v)))


def _http_error(e: StoreError) -> HTTPException:
    code = 404 if e.code == "not_found" else 422 if e.code.startswith("invalid") else 409
    return HTTPException(status_code=code, detail=e.message)


@app.patch("/api/appointments/{appointment_id}")
def edit_appointment(appointment_id: str, patch: AppointmentPatch):
    before = store.appointment(appointment_id)
    try:
        after = store.update(appointment_id, **patch.model_dump(exclude_none=True))
    except StoreError as e:
        raise _http_error(e) from None
    kind = "cancelled" if before and before["status"] == "booked" and after["status"] == "cancelled" else "updated"
    bus.publish("appointment", {"session": "desk", "kind": kind, "appointment": after})
    return after


@app.post("/api/appointments")
def create_appointment(body: AppointmentCreate):
    try:
        appt = store.book(body.doctor_id, body.day, body.start, body.phone, body.patient_name, source="desk", reason=body.reason or None)
    except StoreError as e:
        raise _http_error(e) from None
    bus.publish("appointment", {"session": "desk", "kind": "booked", "appointment": appt})
    return appt


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
