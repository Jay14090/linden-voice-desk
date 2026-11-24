"""The three tools the agent can call, plus their schemas.

Each tool is a plain function over the Store that returns a dict, so it can be
unit-tested without any audio. `register_tools` wires them into a Pipecat LLM
service and publishes what happened to the dashboard.
"""

from __future__ import annotations

import re
import time as _time
from datetime import date, timedelta
from typing import Any, Callable

from loguru import logger
from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import EndWorkerFrame, FunctionCallResultProperties
from pipecat.services.llm_service import FunctionCallParams

from .events import bus
from .store import DOCTORS, Store, StoreError, _words_to_digits as _words_spoken_to_digits, format_phone, normalize_phone, today

DOCTOR_IDS = [d[0] for d in DOCTORS]
MAX_DAYS_OFFERED = 3
MAX_SLOTS_PER_DAY = 3


# ---------- speech-friendly formatting ----------

def spoken_time(hhmm: str) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12} {suffix}" if m == 0 else f"{h12}:{m:02d} {suffix}"


def spoken_day(iso: str) -> str:
    d = date.fromisoformat(iso)
    delta = (d - today()).days
    if delta == 0:
        return f"today, {d.strftime('%A')} {d.day} {d.strftime('%B')}"
    if delta == 1:
        return f"tomorrow, {d.strftime('%A')} {d.day} {d.strftime('%B')}"
    return f"{d.strftime('%A')} {d.day} {d.strftime('%B')}"


def _doctor_brief(d: dict) -> dict:
    return {"doctor_id": d["id"], "name": d["name"], "specialty": d["specialty"], "room": d["room"]}


def _appointment_brief(a: dict) -> dict:
    return {
        "reference": a["id"],
        "doctor": a["doctor_name"],
        "specialty": a["specialty"],
        "date": a["day"],
        "day": spoken_day(a["day"]),
        "time": spoken_time(a["start"]),
        "room": a["room"],
        "status": a["status"],
    }


def _phone_error(raw: Any) -> dict:
    return {
        "ok": False,
        "error": "invalid_phone",
        "message": f"'{raw}' is not a 10-digit mobile number. Ask the caller to say it again, digit by digit.",
    }


# ---------- tools ----------

def check_doctor_availability(store: Store, doctor_id: str | None = None, specialty: str | None = None, date: str | None = None) -> dict:
    if doctor_id and doctor_id in DOCTOR_IDS:
        doctors = [d for d in store.doctors() if d["id"] == doctor_id]
    else:
        doctors = store.find_doctors(doctor_id or specialty)
    if not doctors:
        return {
            "ok": False,
            "error": "no_matching_doctor",
            "message": "No doctor matches that. Offer the list of specialties we have.",
            "doctors": [_doctor_brief(d) for d in store.doctors()],
        }

    requested_day = None
    if date:
        try:
            requested_day = _parse_day(date).isoformat()
        except ValueError:
            return {"ok": False, "error": "bad_date", "message": "Could not read that date. Use YYYY-MM-DD."}

    results = []
    for d in doctors:
        free = store.slots(doctor_id=d["id"], only_free=True)
        by_day: dict[str, list[str]] = {}
        for s in free:
            by_day.setdefault(s["day"], []).append(s["start"])

        entry: dict[str, Any] = _doctor_brief(d)
        if requested_day:
            times = by_day.get(requested_day, [])
            entry["requested_date"] = {
                "date": requested_day,
                "day": spoken_day(requested_day),
                "free_times": times[:MAX_SLOTS_PER_DAY],
                "spoken": [spoken_time(t) for t in times[:MAX_SLOTS_PER_DAY]],
                "more": max(0, len(times) - MAX_SLOTS_PER_DAY),
                "all_free_times": times,  # so a specific time the caller asks for can be checked
            }
            if not times:
                later = [(k, v) for k, v in by_day.items() if k > requested_day]
                nxt = later[0] if later else next(iter(by_day.items()), None)
                entry["next_available"] = (
                    {"date": nxt[0], "day": spoken_day(nxt[0]), "free_times": nxt[1][:MAX_SLOTS_PER_DAY], "spoken": [spoken_time(t) for t in nxt[1][:MAX_SLOTS_PER_DAY]]}
                    if nxt else None
                )
        else:
            days = list(by_day.items())[:MAX_DAYS_OFFERED]
            entry["upcoming"] = [
                {"date": day, "day": spoken_day(day), "free_times": times[:MAX_SLOTS_PER_DAY], "spoken": [spoken_time(t) for t in times[:MAX_SLOTS_PER_DAY]], "more": max(0, len(times) - MAX_SLOTS_PER_DAY)}
                for day, times in days
            ]
        results.append(entry)

    return {"ok": True, "today": today().isoformat(), "doctors": results}


def book_appointment(
    store: Store,
    phone_number: str,
    phone_confirmed: bool,
    doctor_id: str,
    date: str,
    time: str,
    patient_name: str | None = None,
    reason: str | None = None,
) -> dict:
    phone = normalize_phone(phone_number)
    if not phone:
        return _phone_error(phone_number)
    if not phone_confirmed:
        return {
            "ok": False,
            "error": "phone_not_confirmed",
            "message": f"Read the number back first ({_digits_spoken(phone)}) and wait for the caller to say yes.",
        }
    if doctor_id not in DOCTOR_IDS:
        return {"ok": False, "error": "unknown_doctor", "message": "Unknown doctor_id."}
    try:
        day = _parse_day(date).isoformat()
        start = _parse_time(time)
    except ValueError:
        return {"ok": False, "error": "bad_date_or_time", "message": "Use date YYYY-MM-DD and time HH:MM (24-hour)."}

    try:
        appt = store.book(doctor_id, day, start, phone, (patient_name or "").strip() or None, reason=(reason or "").strip() or None)
    except StoreError as e:
        alternatives = [s["start"] for s in store.slots(doctor_id=doctor_id, day=day, only_free=True)][:MAX_SLOTS_PER_DAY]
        return {
            "ok": False,
            "error": e.code,
            "message": e.message,
            "other_free_times_that_day": alternatives,
            "spoken": [spoken_time(t) for t in alternatives],
        }
    return {"ok": True, "appointment": _appointment_brief(appt), "phone": format_phone(phone), "_record": appt}


def cancel_appointment(
    store: Store,
    phone_number: str,
    phone_confirmed: bool,
    reference: str | None = None,
    doctor_id: str | None = None,
    date: str | None = None,
) -> dict:
    phone = normalize_phone(phone_number)
    if not phone:
        return _phone_error(phone_number)
    if not phone_confirmed:
        return {
            "ok": False,
            "error": "phone_not_confirmed",
            "message": f"Read the number back first ({_digits_spoken(phone)}) and wait for the caller to say yes.",
        }

    active = [a for a in store.appointments(phone=phone, include_cancelled=False) if a["day"] >= today().isoformat()]
    if not active:
        return {"ok": False, "error": "no_appointment", "message": f"No upcoming appointment is booked under {format_phone(phone)}."}

    matches = active
    if reference:
        matches = [a for a in matches if a["id"].lower() == reference.strip().lower()] or matches
    if doctor_id:
        matches = [a for a in matches if a["doctor_id"] == doctor_id] or []
    if date:
        try:
            day = _parse_day(date).isoformat()
            matches = [a for a in matches if a["day"] == day]
        except ValueError:
            pass

    if not matches:
        return {
            "ok": False,
            "error": "no_match",
            "message": "None of the caller's appointments match that. Read them out and ask which one.",
            "appointments": [_appointment_brief(a) for a in active],
        }
    if len(matches) > 1:
        return {
            "ok": False,
            "error": "multiple",
            "message": "The caller has more than one appointment. Ask which one to cancel, then call again with doctor_id and date.",
            "appointments": [_appointment_brief(a) for a in matches],
        }

    cancelled = store.cancel(matches[0]["id"])
    return {"ok": True, "cancelled": _appointment_brief(cancelled), "_record": cancelled}


# ---------- parsing ----------

def _parse_day(value: str) -> date:
    v = value.strip().lower()
    if v == "today":
        return today()
    if v == "tomorrow":
        return today() + timedelta(days=1)
    return date.fromisoformat(v[:10])


def _parse_time(value: str) -> str:
    v = value.strip().upper().replace(".", ":")
    pm = v.endswith("PM")
    am = v.endswith("AM")
    v = v.replace("AM", "").replace("PM", "").strip()
    if ":" not in v:
        v = f"{v}:00"
    h, m = (int(x) for x in v.split(":")[:2])
    if pm and h < 12:
        h += 12
    if am and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(value)
    return f"{h:02d}:{m:02d}"


def _digits_spoken(phone: str) -> str:
    return " ".join(phone[:5]) + ", " + " ".join(phone[5:])


# ---------- Pipecat wiring ----------

_DOCTOR_ENUM = {"type": "string", "enum": DOCTOR_IDS, "description": "Doctor id from the roster."}
_PHONE = {"type": "string", "description": "The caller's 10-digit mobile number as they said it, digits only."}
_CONFIRMED = {
    "type": "boolean",
    "description": "True only after you read the number back digit by digit and the caller said it is correct.",
}

TOOLS = ToolsSchema(
    standard_tools=[
        FunctionSchema(
            name="check_doctor_availability",
            description="Get free appointment slots. Pass doctor_id if the caller named a doctor, or specialty if they described a need. Add date to check one day.",
            properties={
                "doctor_id": _DOCTOR_ENUM,
                "specialty": {"type": "string", "description": "Specialty or need, e.g. 'skin', 'cardiologist', 'child'."},
                "date": {"type": "string", "description": "Day to check, YYYY-MM-DD."},
            },
            required=[],
        ),
        FunctionSchema(
            name="book_appointment",
            description="Book a free slot for the caller after they chose a doctor, day and time and confirmed their phone number.",
            properties={
                "phone_number": _PHONE,
                "phone_confirmed": _CONFIRMED,
                "doctor_id": _DOCTOR_ENUM,
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "time": {"type": "string", "description": "Slot start, HH:MM 24-hour, exactly as returned by check_doctor_availability."},
                "patient_name": {"type": "string", "description": "Patient's name if the caller gave it."},
                "reason": {"type": "string", "description": "The caller's problem in a few words, e.g. 'itchy rash on arm for 3 days'. Include the age if it's a child."},
            },
            required=["phone_number", "phone_confirmed", "doctor_id", "date", "time"],
        ),
        FunctionSchema(
            name="cancel_appointment",
            description="Cancel the caller's upcoming appointment, found by their confirmed phone number. If they have several, pass doctor_id and date to pick one.",
            properties={
                "phone_number": _PHONE,
                "phone_confirmed": _CONFIRMED,
                "reference": {"type": "string", "description": "Booking reference like APT-1A2B3C, if the caller has it."},
                "doctor_id": _DOCTOR_ENUM,
                "date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            required=["phone_number", "phone_confirmed"],
        ),
        FunctionSchema(
            name="end_call",
            description="Hang up. Call it in the same reply as your goodbye, once the caller says they're done.",
            properties={},
            required=[],
        ),
    ]
)

def end_call(store: Store) -> dict:
    return {"ok": True}


_GOODBYE = re.compile(
    r"\b(bye|goodbye|good bye|that'?s all|that is all|that'?s it|nothing else|no,? thanks?|no thank you|"
    r"i'?m done|we'?re done|all done|hang up|end (the )?call|see you|have a (good|nice|great) day|take care)\b",
    re.I,
)

NOT_DONE = {
    "ok": False,
    "error": "caller_not_done",
    "message": "The caller hasn't said goodbye. Don't hang up. Ask if there's anything else you can help with.",
}


def caller_said_goodbye(text: str) -> bool:
    return bool(_GOODBYE.search(text or ""))


_IMPLS: dict[str, Callable[..., dict]] = {
    "check_doctor_availability": check_doctor_availability,
    "book_appointment": book_appointment,
    "cancel_appointment": cancel_appointment,
    "end_call": end_call,
}


def readback_unanswered(name: str, args: dict, agent_text_this_turn: str) -> dict | None:
    """Block a booking/cancel made in the same breath as the read-back question.

    The model sometimes says "...4 3 2 1 0. Is that right?" and calls the tool in
    the same response, before the caller has answered. If the text it produced
    since the caller last spoke contains the number and a question, refuse.
    """
    if name not in ("book_appointment", "cancel_appointment") or not agent_text_this_turn:
        return None
    phone = normalize_phone(args.get("phone_number"))
    if not phone:
        return None
    said = re.sub(r"\D", "", _words_spoken_to_digits(agent_text_this_turn))
    if phone in said and "?" in agent_text_this_turn:
        return {
            "ok": False,
            "error": "awaiting_caller",
            "message": "You just asked the caller to confirm the number. Stop and wait for their answer before calling this again. Say nothing more now.",
        }
    return None


def run_tool(store: Store, name: str, args: dict, agent_text_this_turn: str = "") -> dict:
    """Run a tool by name, never raising: errors become a result the LLM can read."""
    blocked = readback_unanswered(name, args or {}, agent_text_this_turn)
    if blocked:
        return blocked
    impl = _IMPLS[name]
    allowed = impl.__code__.co_varnames[1 : impl.__code__.co_argcount]
    clean = {k: v for k, v in (args or {}).items() if k in allowed and v not in (None, "")}
    try:
        return impl(store, **clean)
    except TypeError as e:
        return {"ok": False, "error": "bad_arguments", "message": str(e)}
    except Exception as e:  # noqa: BLE001 - surface to the model, don't crash the call
        logger.exception(f"tool {name} failed")
        return {"ok": False, "error": "internal", "message": f"Something went wrong: {e}"}


def register_tools(llm, store: Store, session_id: str, turn_text=None, before_hang_up=None) -> None:
    async def handler(params: FunctionCallParams):
        name = params.function_name
        args = dict(params.arguments or {})
        started = _time.perf_counter()
        bus.publish("tool", {"session": session_id, "id": params.tool_call_id, "name": name, "args": args, "state": "running"})

        result = run_tool(store, name, args, turn_text.text if turn_text else "")
        record = result.pop("_record", None)
        ms = round((_time.perf_counter() - started) * 1000)
        logger.info(f"tool {name}({args}) -> ok={result.get('ok')} in {ms}ms")

        bus.publish("tool", {"session": session_id, "id": params.tool_call_id, "name": name, "args": args, "state": "done", "ok": result.get("ok"), "error": result.get("error"), "ms": ms})
        if record:
            kind = "booked" if name == "book_appointment" else "cancelled"
            bus.publish("appointment", {"session": session_id, "kind": kind, "appointment": record})
            # the phone UI shows a confirmation card from this
            try:
                await params.pipeline_worker.rtvi.send_server_message({"type": f"appointment.{kind}", "appointment": record})
            except Exception as e:  # noqa: BLE001
                logger.warning(f"could not notify client: {e}")

        if name == "end_call" and turn_text is not None and not caller_said_goodbye(turn_text.last_caller):
            # "Okay" or "thanks" isn't a goodbye: keep the line open.
            await params.result_callback(dict(NOT_DONE))
            return

        if name == "end_call":
            # Don't run the LLM again; flush the goodbye that's already queued, then close.
            await params.result_callback(result, properties=FunctionCallResultProperties(run_llm=False))
            if before_hang_up:
                await before_hang_up()  # says goodbye if the agent didn't
            await params.llm.push_frame(EndWorkerFrame(reason="agent ended the call"))
            return

        await params.result_callback(result)

    for name in _IMPLS:
        llm.register_function(name, handler)
