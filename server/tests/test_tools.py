from datetime import date, datetime, timedelta

import pytest

from app import store as store_mod
from app import tools
from app.store import Store, normalize_phone

# Freeze "now" to Monday 7:00 AM IST so slot availability is deterministic.
FROZEN = datetime(2026, 11, 23, 7, 0, tzinfo=store_mod.TZ)
MON = FROZEN.date()


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    monkeypatch.setattr(store_mod, "now", lambda: FROZEN)
    monkeypatch.setattr(store_mod, "today", lambda: MON)
    monkeypatch.setattr(tools, "today", lambda: MON)


@pytest.fixture
def s():
    st = Store(":memory:")
    st.seed(MON)
    return st


def iso(offset: int) -> str:
    return (MON + timedelta(days=offset)).isoformat()


# ---------- phone handling ----------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("9876543210", "9876543210"),
        ("+91 98765 43210", "9876543210"),
        ("098765-43210", "9876543210"),
        ("9 8 7 6 5 4 3 2 1 0", "9876543210"),
        ("nine eight seven six five, four three two one zero", "9876543210"),
        ("nine double eight seven six triple four one oh", "9887644410"),
        ("+91 nine 8 7 6 5 4 3 2 1 0", "9876543210"),
        ("12345", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


# ---------- seed ----------

def test_seed_respects_working_days(s):
    # Dr. Rao (cardiology) works Mon/Wed/Fri only
    days = {date.fromisoformat(x["day"]).weekday() for x in s.slots(doctor_id="rao")}
    assert days <= {0, 2, 4}


def test_seed_bookings_occupy_slots(s):
    booked = [x for x in s.slots(doctor_id="mehta", day=iso(0)) if not x["free"]]
    assert [x["start"] for x in booked] == ["10:00", "18:00"]


# ---------- check_doctor_availability ----------

def test_availability_by_doctor_id(s):
    r = tools.check_doctor_availability(s, doctor_id="mehta")
    assert r["ok"]
    [doc] = r["doctors"]
    assert doc["name"] == "Dr. Ananya Mehta"
    first_day = doc["upcoming"][0]
    assert "10:00" not in first_day["free_times"]  # already booked
    assert len(first_day["free_times"]) <= tools.MAX_SLOTS_PER_DAY


def test_availability_by_specialty_alias(s):
    r = tools.check_doctor_availability(s, specialty="skin")
    assert [d["doctor_id"] for d in r["doctors"]] == ["iyer"]


def test_availability_on_a_day_off_suggests_next(s):
    r = tools.check_doctor_availability(s, doctor_id="rao", date=iso(1))  # Tuesday
    [doc] = r["doctors"]
    assert doc["requested_date"]["free_times"] == []
    assert doc["next_available"]["date"] == iso(2)


def test_availability_unknown(s):
    r = tools.check_doctor_availability(s, specialty="astrologer")
    assert not r["ok"] and r["error"] == "no_matching_doctor"


def test_past_slots_hidden(s, monkeypatch):
    late = datetime(2026, 11, 23, 12, 10, tzinfo=store_mod.TZ)
    monkeypatch.setattr(store_mod, "now", lambda: late)
    times = [x["start"] for x in s.slots(doctor_id="mehta", day=iso(0))]
    assert times and min(times) >= "12:30"


# ---------- book_appointment ----------

def test_book_requires_confirmed_phone(s):
    r = tools.book_appointment(s, "9876543210", False, "mehta", iso(0), "11:00", "Pat")
    assert r["error"] == "phone_not_confirmed"
    assert "9 8 7 6 5" in r["message"]


def test_book_rejects_bad_phone(s):
    r = tools.book_appointment(s, "98765", True, "mehta", iso(0), "11:00", "Pat")
    assert r["error"] == "invalid_phone"


def test_book_success_and_slot_becomes_taken(s):
    r = tools.book_appointment(s, "+91 99999 00000", True, "mehta", iso(0), "11:00", "Jay")
    assert r["ok"]
    assert r["appointment"]["time"] == "11 AM"
    assert r["_record"]["phone"] == "9999900000"
    again = tools.book_appointment(s, "8888800000", True, "mehta", iso(0), "11:00", "Pat")
    assert again["error"] == "slot_taken"
    assert "11:00" not in again["other_free_times_that_day"]


def test_book_accepts_12h_time(s):
    r = tools.book_appointment(s, "9999900000", True, "mehta", iso(0), "5:30 PM", "Pat")
    assert r["ok"] and r["_record"]["start"] == "17:30"


def test_book_nonexistent_slot(s):
    r = tools.book_appointment(s, "9999900000", True, "rao", iso(1), "10:00", "Pat")  # Rao off on Tuesday
    assert r["error"] == "no_such_slot"


# ---------- cancel_appointment ----------

def test_cancel_single(s):
    tools.book_appointment(s, "9999900000", True, "iyer", iso(1), "12:00", "Pat")
    r = tools.cancel_appointment(s, "99999 00000", True, cancel_confirmed=True)
    assert r["ok"] and r["cancelled"]["status"] == "cancelled"
    # the slot is free again
    assert any(x["start"] == "12:00" and x["free"] for x in s.slots(doctor_id="iyer", day=iso(1)))


def test_cancel_multiple_asks_which(s):
    tools.book_appointment(s, "9999900000", True, "iyer", iso(1), "12:00", "Pat")
    tools.book_appointment(s, "9999900000", True, "mehta", iso(2), "10:00", "Pat")
    r = tools.cancel_appointment(s, "9999900000", True)
    assert r["error"] == "multiple" and len(r["appointments"]) == 2
    r = tools.cancel_appointment(s, "9999900000", True, cancel_confirmed=True, doctor_id="mehta")
    assert r["ok"] and r["cancelled"]["doctor"] == "Dr. Ananya Mehta"


def test_cancel_none(s):
    r = tools.cancel_appointment(s, "7777700000", True)
    assert r["error"] == "no_appointment"


def test_cancel_requires_confirmation(s):
    r = tools.cancel_appointment(s, "9876501234", False)
    assert r["error"] == "phone_not_confirmed"


# ---------- dispatcher ----------

def test_run_tool_drops_unknown_args_and_never_raises(s):
    r = tools.run_tool(s, "check_doctor_availability", {"doctor_id": "das", "bogus": 1})
    assert r["ok"]
    r = tools.run_tool(s, "book_appointment", {"phone_number": "9999900000"})
    assert r["ok"] is False and r["error"] == "bad_arguments"


def test_spoken_formats():
    assert tools.spoken_time("09:30") == "9:30 AM"
    assert tools.spoken_time("17:00") == "5 PM"
    assert tools.spoken_time("12:00") == "12 PM"
    assert tools.spoken_day(iso(1)).startswith("tomorrow")


# ---------- read-back guard ----------

def test_guard_blocks_booking_in_same_breath_as_readback(s):
    args = {"phone_number": "9820123457", "phone_confirmed": True, "doctor_id": "mehta", "date": iso(0), "time": "11:00", "patient_name": "Ravi"}
    r = tools.run_tool(s, "book_appointment", args, "Your number is 9 8 2 0 1, 2 3 4 5 7. Is that right?")
    assert r["error"] == "awaiting_caller"
    r = tools.run_tool(s, "book_appointment", args, "nine eight two zero one, two three four five seven. Correct?")
    assert r["error"] == "awaiting_caller"


def test_guard_allows_after_caller_answered(s):
    args = {"phone_number": "9820123457", "phone_confirmed": True, "doctor_id": "mehta", "date": iso(0), "time": "11:00", "patient_name": "Ravi"}
    assert tools.run_tool(s, "book_appointment", args, "")["ok"]


def test_guard_allows_statement_without_question(s):
    args = {"phone_number": "9820123457", "phone_confirmed": True, "doctor_id": "mehta", "date": iso(0), "time": "11:30", "patient_name": "Ravi"}
    assert tools.run_tool(s, "book_appointment", args, "Booking that under 98201 23457 now.")["ok"]


# ---------- front-desk edits ----------

def test_book_records_reason(s):
    r = tools.book_appointment(s, "9999900000", True, "iyer", iso(1), "12:00", "Jay", reason="itchy rash")
    assert r["_record"]["reason"] == "itchy rash"


def test_update_details_and_reschedule(s):
    appt = tools.book_appointment(s, "9999900000", True, "iyer", iso(1), "12:00", "Jay")["_record"]
    out = s.update(appt["id"], patient_name="Jay C", phone="+91 88888 00000", reason="skin allergy", day=iso(1), start="12:30")
    assert (out["patient_name"], out["phone"], out["reason"], out["start"]) == ("Jay C", "8888800000", "skin allergy", "12:30")
    # the old slot is free again, the new one is taken
    by_start = {x["start"]: x["free"] for x in s.slots(doctor_id="iyer", day=iso(1))}
    assert by_start["12:00"] and not by_start["12:30"]


def test_update_rejects_taken_slot_and_bad_phone(s):
    a = tools.book_appointment(s, "9999900000", True, "iyer", iso(1), "12:00", "Pat")["_record"]
    tools.book_appointment(s, "8888800000", True, "iyer", iso(1), "12:30", "Pat")
    with pytest.raises(store_mod.StoreError) as e:
        s.update(a["id"], start="12:30")
    assert e.value.code == "slot_taken"
    with pytest.raises(store_mod.StoreError):
        s.update(a["id"], phone="123")


def test_cancel_and_restore(s):
    a = tools.book_appointment(s, "9999900000", True, "iyer", iso(1), "12:00", "Pat")["_record"]
    assert s.update(a["id"], status="cancelled")["status"] == "cancelled"
    assert s.update(a["id"], status="booked")["status"] == "booked"


def test_manual_booking_source(s):
    a = s.book("mehta", iso(1), "10:00", "9999900000", "Walk-in", source="desk", reason="fever")
    assert a["source"] == "desk" and a["reason"] == "fever"


def test_cancel_ignores_appointments_already_past_today(s, monkeypatch):
    # Aditya's seeded 9:30 today is over by noon, so there's nothing to cancel
    late = datetime(2026, 11, 23, 12, 0, tzinfo=store_mod.TZ)
    monkeypatch.setattr(store_mod, "now", lambda: late)
    r = tools.cancel_appointment(s, "9001122334", True)
    assert r["error"] == "no_appointment"



# ---------- confirmations ----------

def test_book_needs_a_name(s):
    r = tools.book_appointment(s, "9999900000", True, "mehta", iso(0), "11:00")
    assert r["error"] == "name_missing"


def test_book_readback_message_has_name_and_number(s):
    r = tools.book_appointment(s, "9999900000", False, "mehta", iso(0), "11:00", "Jay")
    assert r["error"] == "phone_not_confirmed" and "Jay" in r["message"]


def test_cancel_looks_up_first_and_asks(s):
    tools.book_appointment(s, "9999900000", True, "iyer", iso(1), "12:00", "Jay")
    r = tools.cancel_appointment(s, "9999900000", True)
    assert r["error"] == "needs_confirmation"
    assert "Dr. Kavya Iyer" in r["message"] and "12 PM" in r["message"]
    # nothing cancelled yet
    assert any(a["status"] == "booked" for a in s.appointments(phone="9999900000"))
    r = tools.cancel_appointment(s, "9999900000", True, cancel_confirmed=True, doctor_id=r["doctor_id"], date=r["date"])
    assert r["ok"]


def test_cancel_confirmation_blocked_in_same_breath(s):
    tools.book_appointment(s, "9999900000", True, "iyer", iso(1), "12:00", "Jay")
    r = tools.run_tool(s, "cancel_appointment", {"phone_number": "9999900000", "phone_confirmed": True, "cancel_confirmed": True},
                       "Do you want to cancel your appointment with Dr. Iyer on Tuesday at 12 PM?")
    assert r["error"] == "awaiting_caller"


# ---------- reschedule ----------

def test_reschedule_flow(s):
    tools.book_appointment(s, "9999900000", True, "iyer", iso(1), "12:00", "Jay")
    r = tools.reschedule_appointment(s, "9999900000", True, iso(1), "13:00")
    assert r["error"] == "needs_confirmation" and "from" in r["message"] and "1 PM" in r["message"]
    r = tools.reschedule_appointment(s, "9999900000", True, iso(1), "13:00", change_confirmed=True)
    assert r["ok"] and r["_record"]["start"] == "13:00"
    free = {x["start"]: x["free"] for x in s.slots(doctor_id="iyer", day=iso(1))}
    assert free["12:00"] and not free["13:00"]


def test_reschedule_to_taken_or_missing_slot(s):
    tools.book_appointment(s, "9999900000", True, "iyer", iso(1), "12:00", "Jay")
    tools.book_appointment(s, "8888800000", True, "iyer", iso(1), "12:30", "Other")
    r = tools.reschedule_appointment(s, "9999900000", True, iso(1), "12:30", change_confirmed=True)
    assert r["error"] == "slot_unavailable" and "12:30" not in r["free_times_that_day"]
    r = tools.reschedule_appointment(s, "9999900000", True, iso(1), "08:00", change_confirmed=True)
    assert r["error"] == "slot_unavailable"
    r = tools.reschedule_appointment(s, "9999900000", True, iso(1), "12:00", change_confirmed=True)
    assert r["error"] == "same_time"


def test_reschedule_needs_confirmed_phone_and_existing_booking(s):
    r = tools.reschedule_appointment(s, "9999900000", False, iso(1), "13:00")
    assert r["error"] == "phone_not_confirmed"
    r = tools.reschedule_appointment(s, "7777700000", True, iso(1), "13:00")
    assert r["error"] == "no_appointment"


def test_reschedule_to_another_doctor(s):
    tools.book_appointment(s, "9999900000", True, "mehta", iso(1), "10:30", "Jay")
    r = tools.reschedule_appointment(s, "9999900000", True, iso(1), "12:30", change_confirmed=True, new_doctor_id="iyer")
    assert r["ok"] and r["_record"]["doctor_id"] == "iyer"


def test_tracker_requires_spoken_readback():
    t = tools.ConfirmationTracker()
    args = {"phone_number": "9182736450", "phone_confirmed": True, "patient_name": "Jay"}
    assert t.check("book_appointment", args)["error"] == "phone_not_confirmed"  # never read back
    t.agent_said("That's Jay, 91827 36450. Is that right?")
    assert t.check("book_appointment", args)  # caller hasn't answered yet
    t.caller_spoke()
    assert t.check("book_appointment", args) is None
    assert t.check("book_appointment", {**args, "phone_number": "9999900000"})  # a different number


def test_tracker_cancel_and_move_need_the_question():
    t = tools.ConfirmationTracker()
    t.agent_said("I have 91827 36450. Is that right?")
    t.caller_spoke()
    c = {"phone_number": "9182736450", "phone_confirmed": True, "cancel_confirmed": True}
    assert t.check("cancel_appointment", c)["error"] == "not_asked"
    t.agent_said("Do you want to cancel your appointment with Dr. Das on Thursday at 11 AM?")
    t.caller_spoke()
    assert t.check("cancel_appointment", c) is None
    m = {"phone_number": "9182736450", "phone_confirmed": True, "change_confirmed": True}
    assert t.check("reschedule_appointment", m)["error"] == "not_asked"
