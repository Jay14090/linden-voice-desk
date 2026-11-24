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


def store_now():
    # looked up at call time so tests can freeze the clock
    from . import store as _s

    return _s.now()


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
    if not (patient_name or "").strip():
        return {"ok": False, "error": "name_missing", "message": "Ask for the patient's name first."}
    if not phone_confirmed:
        return {
            "ok": False,
            "error": "phone_not_confirmed",
            "message": f"Read the name and number back together (\"{(patient_name or '').strip()}, {_digits_spoken(phone)}\") and wait for the caller to say yes.",
        }
    if doctor_id not in DOCTOR_IDS:
        return {"ok": False, "error": "unknown_doctor", "message": "Unknown doctor_id."}
    try:
        day = _parse_day(date).isoformat()
        start = _parse_time(time)
    except ValueError:
        return {"ok": False, "error": "bad_date_or_time", "message": "Use date YYYY-MM-DD and time HH:MM (24-hour)."}

    try:
        appt = store.book(doctor_id, day, start, phone, (patient_name or "").strip()[:80] or None, reason=((reason or "").strip()[:300]) or None)
    except StoreError as e:
        alternatives = [s["start"] for s in store.slots(doctor_id=doctor_id, day=day, only_free=True)][:MAX_SLOTS_PER_DAY]
        return {
            "ok": False,
            "error": e.code,
            "message": e.message,
            "other_free_times_that_day": alternatives,
            "spoken": [spoken_time(t) for t in alternatives],
        }
    return {
        "ok": True,
        "appointment": _appointment_brief(appt),
        "phone": format_phone(phone),
        "next": "If the caller asked for another appointment earlier in this call and chose its time, book it now with this same confirmed number, then confirm both together.",
        "_record": appt,
    }


def _phone_gate(phone_number: str, phone_confirmed: bool) -> tuple[str | None, dict | None]:
    phone = normalize_phone(phone_number)
    if not phone:
        return None, _phone_error(phone_number)
    if not phone_confirmed:
        return None, {
            "ok": False,
            "error": "phone_not_confirmed",
            "message": f"Read the number back first ({_digits_spoken(phone)}) and wait for the caller to say yes.",
        }
    return phone, None


def _find_caller_appointment(store: Store, phone: str, reference, doctor_id, date, verb: str) -> tuple[dict | None, dict | None]:
    """The one upcoming appointment the caller means, or an error result telling the agent what to ask."""
    now_key = store_now().strftime("%Y-%m-%d %H:%M")
    active = [a for a in store.appointments(phone=phone, include_cancelled=False) if f"{a['day']} {a['start']}" > now_key]
    if not active:
        return None, {"ok": False, "error": "no_appointment", "message": f"No upcoming appointment is booked under {format_phone(phone)}."}

    matches = active
    if reference:
        matches = [a for a in matches if a["id"].lower() == reference.strip().lower()] or matches
    if doctor_id:
        matches = [a for a in matches if a["doctor_id"] == doctor_id]
    if date:
        try:
            day = _parse_day(date).isoformat()
            matches = [a for a in matches if a["day"] == day]
        except ValueError:
            pass

    if not matches:
        return None, {
            "ok": False,
            "error": "no_match",
            "message": "None of the caller's appointments match that. Read them out and ask which one.",
            "appointments": [_appointment_brief(a) | {"doctor_id": a["doctor_id"]} for a in active],
        }
    if len(matches) > 1:
        return None, {
            "ok": False,
            "error": "multiple",
            "message": f"The caller has more than one appointment. Read them out briefly and ask which one to {verb}, then call again with that doctor_id and date.",
            "appointments": [_appointment_brief(a) | {"doctor_id": a["doctor_id"]} for a in matches],
        }
    return matches[0], None


def cancel_appointment(
    store: Store,
    phone_number: str,
    phone_confirmed: bool,
    cancel_confirmed: bool = False,
    reference: str | None = None,
    doctor_id: str | None = None,
    date: str | None = None,
) -> dict:
    phone, err = _phone_gate(phone_number, phone_confirmed)
    if err:
        return err
    appt, err = _find_caller_appointment(store, phone, reference, doctor_id, date, "cancel")
    if err:
        return err

    brief = _appointment_brief(appt)
    if not cancel_confirmed:
        # never cancel on the first ask: the caller hears exactly what's being cancelled
        return {
            "ok": False,
            "error": "needs_confirmation",
            "appointment": brief,
            "doctor_id": appt["doctor_id"],
            "date": appt["day"],
            "message": (
                f"Ask: \"Do you want to cancel your appointment with {appt['doctor_name']} on {brief['day']} at {brief['time']}?\" "
                "Wait for a clear yes, then call cancel_appointment again with cancel_confirmed true and this doctor_id and date."
            ),
        }
    cancelled = store.cancel(appt["id"])
    return {"ok": True, "cancelled": _appointment_brief(cancelled), "_record": cancelled}


def reschedule_appointment(
    store: Store,
    phone_number: str,
    phone_confirmed: bool,
    new_date: str,
    new_time: str,
    change_confirmed: bool = False,
    new_doctor_id: str | None = None,
    reference: str | None = None,
    doctor_id: str | None = None,
    date: str | None = None,
) -> dict:
    phone, err = _phone_gate(phone_number, phone_confirmed)
    if err:
        return err
    appt, err = _find_caller_appointment(store, phone, reference, doctor_id, date, "move")
    if err:
        return err

    target_doctor = new_doctor_id if new_doctor_id in DOCTOR_IDS else appt["doctor_id"]
    try:
        day = _parse_day(new_date).isoformat()
        start = _parse_time(new_time)
    except ValueError:
        return {"ok": False, "error": "bad_date_or_time", "message": "Use new_date YYYY-MM-DD and new_time HH:MM (24-hour)."}

    current = _appointment_brief(appt)
    if (target_doctor, day, start) == (appt["doctor_id"], appt["day"], appt["start"]):
        return {"ok": False, "error": "same_time", "message": "That's the time they already have. Ask which new time they'd like."}

    free = [s["start"] for s in store.slots(doctor_id=target_doctor, day=day, only_free=True)]
    if start not in free:
        return {
            "ok": False,
            "error": "slot_unavailable",
            "message": "That time isn't free. Offer these instead.",
            "free_times_that_day": free[:MAX_SLOTS_PER_DAY],
            "spoken": [spoken_time(t) for t in free[:MAX_SLOTS_PER_DAY]],
        }

    doctor_name = next(d["name"] for d in store.doctors() if d["id"] == target_doctor)
    proposed = {"doctor": doctor_name, "date": day, "day": spoken_day(day), "time": spoken_time(start)}
    if not change_confirmed:
        return {
            "ok": False,
            "error": "needs_confirmation",
            "current": current,
            "proposed": proposed,
            "doctor_id": appt["doctor_id"],
            "date": appt["day"],
            "message": (
                f"Ask: \"Shall I move your appointment with {appt['doctor_name']} from {current['day']} at {current['time']} "
                f"to {proposed['day']} at {proposed['time']}?\" Wait for a clear yes, then call again with change_confirmed true."
            ),
        }
    try:
        moved = store.update(appt["id"], doctor_id=target_doctor, day=day, start=start)
    except StoreError as e:
        return {"ok": False, "error": e.code, "message": e.message}
    return {"ok": True, "moved_from": current, "appointment": _appointment_brief(moved), "_record": moved}


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
            description=(
                "Cancel the caller's upcoming appointment, found by their confirmed phone number. Call it first with "
                "cancel_confirmed false to look the appointment up, ask the caller to confirm that exact appointment, "
                "then call again with cancel_confirmed true."
            ),
            properties={
                "phone_number": _PHONE,
                "phone_confirmed": _CONFIRMED,
                "cancel_confirmed": {"type": "boolean", "description": "True only after the caller said yes to cancelling that specific appointment."},
                "reference": {"type": "string", "description": "Booking reference like APT-1A2B3C, if the caller has it."},
                "doctor_id": {**_DOCTOR_ENUM, "description": "Doctor of the appointment to cancel, to pick one."},
                "date": {"type": "string", "description": "Day of the appointment to cancel, YYYY-MM-DD."},
            },
            required=["phone_number", "phone_confirmed", "cancel_confirmed"],
        ),
        FunctionSchema(
            name="reschedule_appointment",
            description=(
                "Move the caller's upcoming appointment to a new free time (same doctor unless new_doctor_id is given). "
                "Check availability first, call with change_confirmed false to get the confirmation question, ask it, "
                "then call again with change_confirmed true."
            ),
            properties={
                "phone_number": _PHONE,
                "phone_confirmed": _CONFIRMED,
                "new_date": {"type": "string", "description": "New day, YYYY-MM-DD."},
                "new_time": {"type": "string", "description": "New slot start, HH:MM 24-hour, from check_doctor_availability."},
                "change_confirmed": {"type": "boolean", "description": "True only after the caller said yes to the exact move."},
                "new_doctor_id": {**_DOCTOR_ENUM, "description": "Only if moving to a different doctor."},
                "doctor_id": {**_DOCTOR_ENUM, "description": "Doctor of the current appointment, if they have several."},
                "date": {"type": "string", "description": "Day of the current appointment, YYYY-MM-DD, if they have several."},
            },
            required=["phone_number", "phone_confirmed", "new_date", "new_time", "change_confirmed"],
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
    "reschedule_appointment": reschedule_appointment,
    "end_call": end_call,
}


class ConfirmationTracker:
    """What the agent has actually asked the caller to confirm, turn by turn.

    The model sets phone_confirmed / cancel_confirmed / change_confirmed itself,
    and in testing it sometimes set them without asking. This keeps a record
    from the agent's real words: a confirmation only counts if the question was
    spoken in an earlier turn and the caller has answered since.
    """

    _PHONE_RE = re.compile(r"(?<!\d)(\d{5})[\s-]?(\d{5})(?!\d)")

    def __init__(self) -> None:
        self.current = ""  # agent words since the caller last spoke
        self.read_back: set[str] = set()  # numbers read back with a question, then answered
        self.asked_cancel = False
        self.asked_move = False

    def agent_said(self, text: str) -> None:
        self.current += text

    def caller_spoke(self) -> None:
        said = self.current
        if "?" in said:
            for a, b in self._PHONE_RE.findall(said):
                self.read_back.add(a + b)
            low = said.lower()
            self.asked_cancel = "cancel" in low
            self.asked_move = "move" in low or "reschedul" in low or "change" in low
        else:
            self.asked_cancel = self.asked_move = False
        self.current = ""

    def check(self, name: str, args: dict) -> dict | None:
        if name not in ("book_appointment", "cancel_appointment", "reschedule_appointment"):
            return None
        phone = normalize_phone(args.get("phone_number"))
        if args.get("phone_confirmed") and phone and phone not in self.read_back:
            who = " and the patient's name" if name == "book_appointment" else ""
            return {
                "ok": False,
                "error": "phone_not_confirmed",
                "message": f"You haven't read the number{who} back yet. Say it back ({format_phone(phone)}) and ask if it's right, then wait.",
            }
        if name == "cancel_appointment" and args.get("cancel_confirmed") and not self.asked_cancel:
            return {
                "ok": False,
                "error": "not_asked",
                "message": "Ask the caller to confirm cancelling that exact appointment (doctor, day and time) and wait for yes. Call with cancel_confirmed false to look it up.",
            }
        if name == "reschedule_appointment" and args.get("change_confirmed") and not self.asked_move:
            return {
                "ok": False,
                "error": "not_asked",
                "message": "Ask the caller to confirm the exact move (from and to) and wait for yes. Call with change_confirmed false to get the question.",
            }
        return None


def readback_unanswered(name: str, args: dict, agent_text_this_turn: str) -> dict | None:
    """Block a booking/cancel made in the same breath as the read-back question.

    The model sometimes says "...4 3 2 1 0. Is that right?" and calls the tool in
    the same response, before the caller has answered. If the text it produced
    since the caller last spoke contains the number and a question, refuse.
    """
    if not agent_text_this_turn:
        return None
    if (args.get("cancel_confirmed") or args.get("change_confirmed")) and "?" in agent_text_this_turn:
        return {
            "ok": False,
            "error": "awaiting_caller",
            "message": "You just asked the caller to confirm. Stop and wait for their answer before calling this again. Say nothing more now.",
        }
    if name not in ("book_appointment", "cancel_appointment", "reschedule_appointment"):
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


def run_tool(store: Store, name: str, args: dict, agent_text_this_turn: str = "", tracker: ConfirmationTracker | None = None) -> dict:
    """Run a tool by name, never raising: errors become a result the LLM can read."""
    blocked = readback_unanswered(name, args or {}, agent_text_this_turn) or (tracker.check(name, args or {}) if tracker else None)
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

        result = run_tool(store, name, args, turn_text.text if turn_text else "", getattr(turn_text, "tracker", None))
        record = result.pop("_record", None)
        ms = round((_time.perf_counter() - started) * 1000)
        logger.info(f"tool {name}({args}) -> ok={result.get('ok')} in {ms}ms")

        bus.publish("tool", {"session": session_id, "id": params.tool_call_id, "name": name, "args": args, "state": "done", "ok": result.get("ok"), "error": result.get("error"), "ms": ms})
        if record:
            kind = {"book_appointment": "booked", "cancel_appointment": "cancelled", "reschedule_appointment": "rescheduled"}[name]
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
