"""HTTP-level checks for the dashboard API: every bad input should get a clear 4xx, never a 500."""

import os
import tempfile
from datetime import datetime, timedelta

import pytest

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.pop("ELEVENLABS_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from app import main, store as store_mod  # noqa: E402

FROZEN = datetime(2026, 11, 23, 7, 0, tzinfo=store_mod.TZ)  # Monday 7 AM


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(store_mod, "now", lambda: FROZEN)
    monkeypatch.setattr(store_mod, "today", lambda: FROZEN.date())
    main.store.seed(FROZEN.date())


client = TestClient(main.app, raise_server_exceptions=False)
MON = FROZEN.date().isoformat()
TUE = (FROZEN.date() + timedelta(days=1)).isoformat()


def new(**kw):
    body = {"doctor_id": "mehta", "day": TUE, "start": "10:30", "phone": "9999900000", "patient_name": "Test", "reason": "fever"}
    body.update(kw)
    return client.post("/api/appointments", json=body)


def appt_id(r):
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ---------- create ----------

def test_create_ok():
    r = new()
    assert r.status_code == 200 and r.json()["source"] == "desk"


@pytest.mark.parametrize(
    "override,code",
    [
        ({"doctor_id": "nobody"}, 422),
        ({"day": "2026-13-45"}, 422),
        ({"day": "tomorrow-ish"}, 422),
        ({"start": "25:00"}, 422),
        ({"start": "10:15"}, 409),  # not a slot boundary
        ({"start": "abc"}, 422),
        ({"phone": "12345"}, 422),
        ({"phone": ""}, 422),
        ({"phone": "abcdefghij"}, 422),
        ({"patient_name": ""}, 422),
        ({"patient_name": "   "}, 422),
        ({"patient_name": "x" * 200}, 422),
        ({"reason": "y" * 1000}, 422),
        ({"day": MON, "start": "06:00"}, 409),  # no such slot
        ({"day": (FROZEN.date() - timedelta(days=1)).isoformat()}, 409),  # past day
    ],
)
def test_create_rejects(override, code):
    r = new(**override)
    assert r.status_code == code, r.text
    assert isinstance(r.json().get("detail"), (str, list))


def test_create_taken_slot():
    assert new().status_code == 200
    r = new(phone="8888800000")
    assert r.status_code == 409 and "booked" in r.json()["detail"].lower()


def test_create_past_time_today(monkeypatch):
    late = datetime(2026, 11, 23, 12, 0, tzinfo=store_mod.TZ)
    monkeypatch.setattr(store_mod, "now", lambda: late)
    r = new(day=MON, start="10:30")
    assert r.status_code == 409


# ---------- edit ----------

def test_edit_unknown():
    assert client.patch("/api/appointments/APT-NOPE", json={"reason": "x"}).status_code == 404


@pytest.mark.parametrize(
    "patch,code",
    [
        ({"status": "deleted"}, 422),
        ({"phone": "12"}, 422),
        ({"patient_name": ""}, 422),
        ({"day": "2026-02-30"}, 422),
        ({"start": "9am"}, 422),
        ({"doctor_id": "nobody"}, 422),
        ({"reason": "z" * 1000}, 422),
    ],
)
def test_edit_rejects(patch, code):
    a = appt_id(new())
    r = client.patch(f"/api/appointments/{a}", json=patch)
    assert r.status_code == code, r.text


def test_edit_into_booked_slot_is_refused():
    a = appt_id(new(start="10:30"))
    appt_id(new(start="11:00", phone="8888800000"))
    r = client.patch(f"/api/appointments/{a}", json={"start": "11:00"})
    assert r.status_code == 409 and "booked" in r.json()["detail"].lower()
    # nothing changed
    assert client.get("/api/appointments").json()[[x["id"] for x in client.get("/api/appointments").json()].index(a)]["start"] == "10:30"


def test_edit_change_doctor_only_to_one_without_that_slot():
    a = appt_id(new(start="10:30"))  # Mehta Tuesday 10:30
    r = client.patch(f"/api/appointments/{a}", json={"doctor_id": "rao"})  # Rao doesn't work Tuesdays
    assert r.status_code == 409


def test_edit_cancelled_booking_cannot_be_rescheduled_or_edited_into_slot():
    a = appt_id(new())
    assert client.patch(f"/api/appointments/{a}", json={"status": "cancelled"}).json()["status"] == "cancelled"
    r = client.patch(f"/api/appointments/{a}", json={"start": "11:00"})
    assert r.status_code == 409


def test_restore_when_slot_was_taken():
    a = appt_id(new())
    client.patch(f"/api/appointments/{a}", json={"status": "cancelled"})
    appt_id(new(phone="8888800000"))  # someone else takes the slot
    r = client.patch(f"/api/appointments/{a}", json={"status": "booked"})
    assert r.status_code == 409


def test_restore_past_booking_refused(monkeypatch):
    a = appt_id(new(day=MON, start="10:30"))
    client.patch(f"/api/appointments/{a}", json={"status": "cancelled"})
    late = datetime(2026, 11, 23, 12, 0, tzinfo=store_mod.TZ)
    monkeypatch.setattr(store_mod, "now", lambda: late)
    r = client.patch(f"/api/appointments/{a}", json={"status": "booked"})
    assert r.status_code == 409


def test_cancel_twice_is_harmless():
    a = appt_id(new())
    assert client.patch(f"/api/appointments/{a}", json={"status": "cancelled"}).status_code == 200
    assert client.patch(f"/api/appointments/{a}", json={"status": "cancelled"}).status_code == 200


def test_edit_keeps_own_slot():
    a = appt_id(new())
    r = client.patch(f"/api/appointments/{a}", json={"doctor_id": "mehta", "day": TUE, "start": "10:30", "reason": "cough"})
    assert r.status_code == 200 and r.json()["reason"] == "cough"


def test_malformed_json():
    r = client.patch("/api/appointments/x", content="{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422


def test_html_is_stored_as_text():
    r = new(patient_name="<img src=x onerror=alert(1)>")
    assert r.status_code == 200 and r.json()["patient_name"] == "<img src=x onerror=alert(1)>"
