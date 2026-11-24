"""Clinic data: doctors, their slots and appointments, in SQLite.

Slots are generated for the next seven clinic days relative to "today" in
Asia/Kolkata, so the demo data never goes stale. A slot is free when it has no
active appointment pointing at it.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Kolkata")
SLOT_MINUTES = 30
DAYS_AHEAD = 7

DOCTORS = [
    # id, name, specialty, room, weekdays worked (0 = Mon), shifts
    ("mehta", "Dr. Ananya Mehta", "General Physician", "101", {0, 1, 2, 3, 4, 5}, [("09:30", "13:00"), ("17:00", "19:00")]),
    ("rao", "Dr. Vikram Rao", "Cardiologist", "204", {0, 2, 4}, [("10:00", "13:00")]),
    ("iyer", "Dr. Kavya Iyer", "Dermatologist", "112", {1, 3, 5}, [("11:00", "14:00"), ("16:00", "18:00")]),
    ("sheikh", "Dr. Farhan Sheikh", "Pediatrician", "108", {0, 1, 2, 3, 4}, [("09:00", "12:00")]),
    ("kulkarni", "Dr. Neha Kulkarni", "Orthopedic Surgeon", "210", {1, 2, 4, 5}, [("14:00", "18:00")]),
    ("das", "Dr. Arjun Das", "ENT Specialist", "115", {0, 3, 5}, [("10:00", "12:30"), ("17:30", "19:30")]),
]

# A few existing bookings so the board doesn't open empty: (doctor, day offset, time, phone, name, reason)
SEED_BOOKINGS = [
    ("mehta", 0, "10:00", "9876501234", "Rohan Verma", "Fever and body ache for two days"),
    ("mehta", 0, "18:00", "9930012457", "Kabir Malhotra", "Routine BP check-up"),
    ("mehta", 1, "17:30", "9123456780", "Priya Nair", "Stomach upset"),
    ("iyer", 0, "16:30", "9988776655", "Sana Qureshi", "Acne flare-up"),
    ("iyer", 2, "12:00", "9845098450", "Tara Menon", "Hair fall"),
    ("sheikh", 0, "09:30", "9001122334", "Aditya Joshi", "Child, 6, cough and cold"),
    ("sheikh", 1, "10:30", "9819911223", "Ishaan Gupta", "Child, 3, vaccination follow-up"),
    ("kulkarni", 0, "15:00", "9812345670", "Meera Pillai", "Knee pain after running"),
    ("kulkarni", 1, "16:30", "9767001122", "Dev Bhatia", "Lower back pain"),
    ("rao", 1, "11:00", "9821176543", "Suresh Iyer", "Palpitations"),
    ("das", 0, "18:30", "9898123123", "Nikhil Rane", "Blocked nose and sinus pain"),
]


def now() -> datetime:
    return datetime.now(TZ)


def today() -> date:
    return now().date()


def normalize_phone(raw: str | None) -> str | None:
    """Reduce a spoken/typed phone number to 10 digits, or None if it isn't one.

    Accepts "+91 98765 43210", "098765-43210", "9 8 7 6 5 4 3 2 1 0" and so on.
    """
    if not raw:
        return None
    digits = re.sub(r"\D", "", _words_to_digits(str(raw)))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
_REPEAT = {"double": 2, "triple": 3}


def _words_to_digits(text: str) -> str:
    """'nine double eight 7 ...' -> '9887...'. Indian callers often say 'double'/'triple'."""
    out: list[str] = []
    repeat = 1
    for token in re.findall(r"[a-zA-Z]+|\d", text.lower()):
        if token in _REPEAT:
            repeat = _REPEAT[token]
            continue
        digit = token if token.isdigit() else _DIGIT_WORDS.get(token)
        if digit is not None:
            out.append(digit * repeat)
        repeat = 1
    return "".join(out)


def format_phone(digits: str) -> str:
    return f"{digits[:5]} {digits[5:]}"


@dataclass
class StoreError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    # ---------- setup ----------

    def _create_schema(self) -> None:
        with self._lock, self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS doctors (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    specialty TEXT NOT NULL,
                    room TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS slots (
                    id TEXT PRIMARY KEY,
                    doctor_id TEXT NOT NULL REFERENCES doctors(id),
                    day TEXT NOT NULL,
                    start TEXT NOT NULL,
                    UNIQUE (doctor_id, day, start)
                );
                CREATE TABLE IF NOT EXISTS appointments (
                    id TEXT PRIMARY KEY,
                    slot_id TEXT NOT NULL REFERENCES slots(id),
                    phone TEXT NOT NULL,
                    patient_name TEXT,
                    reason TEXT,
                    status TEXT NOT NULL CHECK (status IN ('booked', 'cancelled')),
                    source TEXT NOT NULL DEFAULT 'voice',
                    created_at TEXT NOT NULL,
                    cancelled_at TEXT
                );
                -- one live booking per slot
                CREATE UNIQUE INDEX IF NOT EXISTS one_booking_per_slot
                    ON appointments(slot_id) WHERE status = 'booked';
                CREATE INDEX IF NOT EXISTS appointments_by_phone ON appointments(phone);
                """
            )
            # databases created before the reason column existed
            cols = {r["name"] for r in self._db.execute("PRAGMA table_info(appointments)")}
            if "reason" not in cols:
                self._db.execute("ALTER TABLE appointments ADD COLUMN reason TEXT")

    def seed(self, start: date | None = None) -> None:
        """Reset to the demo dataset, with slots for the next clinic days."""
        start = start or today()
        with self._lock, self._db:
            self._db.execute("DELETE FROM appointments")
            self._db.execute("DELETE FROM slots")
            self._db.execute("DELETE FROM doctors")
            for doc_id, name, specialty, room, weekdays, shifts in DOCTORS:
                self._db.execute(
                    "INSERT INTO doctors (id, name, specialty, room) VALUES (?, ?, ?, ?)",
                    (doc_id, name, specialty, room),
                )
                for offset in range(DAYS_AHEAD):
                    day = start + timedelta(days=offset)
                    if day.weekday() not in weekdays:
                        continue
                    for shift_start, shift_end in shifts:
                        t = datetime.combine(day, time.fromisoformat(shift_start))
                        end = datetime.combine(day, time.fromisoformat(shift_end))
                        while t < end:
                            self._db.execute(
                                "INSERT INTO slots (id, doctor_id, day, start) VALUES (?, ?, ?, ?)",
                                (_slot_id(doc_id, day, t.strftime("%H:%M")), doc_id, day.isoformat(), t.strftime("%H:%M")),
                            )
                            t += timedelta(minutes=SLOT_MINUTES)
            for doc_id, offset, start_time, phone, name, reason in SEED_BOOKINGS:
                # the doctor may not work that weekday: use their next working day
                for extra in range(DAYS_AHEAD):
                    slot = self._db.execute(
                        "SELECT s.id FROM slots s LEFT JOIN appointments a ON a.slot_id = s.id WHERE s.id = ? AND a.id IS NULL",
                        (_slot_id(doc_id, start + timedelta(days=offset + extra), start_time),),
                    ).fetchone()
                    if slot:
                        self._insert_appointment(slot["id"], phone, name, source="seed", reason=reason)
                        break

    # ---------- reads ----------

    def doctors(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM doctors ORDER BY specialty = 'General Physician' DESC, name").fetchall()
        return [dict(r) for r in rows]

    def find_doctors(self, query: str | None) -> list[dict]:
        """Match a doctor by name or specialty, tolerant of how people say it."""
        docs = self.doctors()
        if not query:
            return docs
        q = _simplify(query)
        if not q:
            return docs
        aliases = {
            "general": "General Physician", "gp": "General Physician", "physician": "General Physician",
            "family": "General Physician", "fever": "General Physician",
            "heart": "Cardiologist", "cardio": "Cardiologist",
            "skin": "Dermatologist", "derma": "Dermatologist",
            "child": "Pediatrician", "kid": "Pediatrician", "paediatric": "Pediatrician", "pediatric": "Pediatrician", "baby": "Pediatrician",
            "bone": "Orthopedic Surgeon", "ortho": "Orthopedic Surgeon", "joint": "Orthopedic Surgeon", "knee": "Orthopedic Surgeon", "back": "Orthopedic Surgeon",
            "ear": "ENT Specialist", "nose": "ENT Specialist", "throat": "ENT Specialist", "ent": "ENT Specialist",
        }
        hits = []
        for d in docs:
            name = _simplify(d["name"].replace("Dr.", ""))
            spec = _simplify(d["specialty"])
            name_parts = name.split()
            if q in name or q in spec or any(p in q.split() for p in name_parts) or d["id"] in q.split():
                hits.append(d)
                continue
            for word, specialty in aliases.items():
                if word in q.split() or q.startswith(word):
                    if d["specialty"] == specialty:
                        hits.append(d)
                        break
        return hits

    def slots(self, doctor_id: str | None = None, day: str | None = None, only_free: bool = False, include_past: bool = False) -> list[dict]:
        sql = """
            SELECT s.id, s.doctor_id, s.day, s.start,
                   a.id AS appointment_id
            FROM slots s
            LEFT JOIN appointments a ON a.slot_id = s.id AND a.status = 'booked'
            WHERE 1 = 1
        """
        args: list = []
        if doctor_id:
            sql += " AND s.doctor_id = ?"
            args.append(doctor_id)
        if day:
            sql += " AND s.day = ?"
            args.append(day)
        sql += " ORDER BY s.day, s.start"
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        current = now()
        out = []
        for r in rows:
            starts_at = datetime.combine(date.fromisoformat(r["day"]), time.fromisoformat(r["start"]), TZ)
            past = starts_at <= current
            if past and not include_past:
                continue  # past slots can't be offered
            free = r["appointment_id"] is None and not past
            if only_free and not free:
                continue
            row = {"id": r["id"], "doctor_id": r["doctor_id"], "day": r["day"], "start": r["start"], "free": free}
            if include_past:
                row["past"] = past
            out.append(row)
        return out

    def appointments(self, phone: str | None = None, include_cancelled: bool = True) -> list[dict]:
        sql = """
            SELECT a.*, s.day, s.start, s.doctor_id, d.name AS doctor_name, d.specialty, d.room
            FROM appointments a
            JOIN slots s ON s.id = a.slot_id
            JOIN doctors d ON d.id = s.doctor_id
            WHERE 1 = 1
        """
        args: list = []
        if phone:
            sql += " AND a.phone = ?"
            args.append(phone)
        if not include_cancelled:
            sql += " AND a.status = 'booked'"
        sql += " ORDER BY s.day, s.start"
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def appointment(self, appointment_id: str) -> dict | None:
        rows = [a for a in self.appointments() if a["id"] == appointment_id]
        return rows[0] if rows else None

    # ---------- writes ----------

    def book(
        self,
        doctor_id: str,
        day: str,
        start: str,
        phone: str,
        patient_name: str | None = None,
        source: str = "voice",
        reason: str | None = None,
    ) -> dict:
        with self._lock, self._db:
            slot_id = self._bookable_slot(doctor_id, day, start)
            try:
                appointment_id = self._insert_appointment(slot_id, phone, patient_name, source, reason)
            except sqlite3.IntegrityError:
                raise StoreError("slot_taken", "That slot was just taken.") from None
        return self.appointment(appointment_id)  # type: ignore[return-value]

    def update(
        self,
        appointment_id: str,
        *,
        patient_name: str | None = None,
        phone: str | None = None,
        reason: str | None = None,
        doctor_id: str | None = None,
        day: str | None = None,
        start: str | None = None,
        status: str | None = None,
    ) -> dict:
        """Edit a booking from the front desk: details, a new slot, cancel or restore."""
        current = self.appointment(appointment_id)
        if not current:
            raise StoreError("not_found", "No appointment with that reference.")
        with self._lock, self._db:
            fields: dict = {}
            if patient_name is not None:
                fields["patient_name"] = patient_name.strip() or None
            if phone is not None:
                digits = normalize_phone(phone)
                if not digits:
                    raise StoreError("invalid_phone", "Phone must be a 10-digit mobile number.")
                fields["phone"] = digits
            if reason is not None:
                fields["reason"] = reason.strip() or None
            if doctor_id or day or start:
                target = (doctor_id or current["doctor_id"], day or current["day"], start or current["start"])
                if target != (current["doctor_id"], current["day"], current["start"]):
                    fields["slot_id"] = self._bookable_slot(*target)
            if status == "cancelled" and current["status"] == "booked":
                fields["status"] = "cancelled"
                fields["cancelled_at"] = now().isoformat(timespec="seconds")
            elif status == "booked" and current["status"] == "cancelled":
                fields["status"] = "booked"
                fields["cancelled_at"] = None
            if fields:
                sets = ", ".join(f"{k} = ?" for k in fields)
                try:
                    self._db.execute(f"UPDATE appointments SET {sets} WHERE id = ?", (*fields.values(), appointment_id))
                except sqlite3.IntegrityError:
                    raise StoreError("slot_taken", "That slot is already booked.") from None
        return self.appointment(appointment_id)  # type: ignore[return-value]

    def _bookable_slot(self, doctor_id: str, day: str, start: str) -> str:
        slot_id = _slot_id(doctor_id, date.fromisoformat(day), start)
        slot = self._db.execute("SELECT id FROM slots WHERE id = ?", (slot_id,)).fetchone()
        if not slot:
            raise StoreError("no_such_slot", "That doctor does not have a slot at that time.")
        starts_at = datetime.combine(date.fromisoformat(day), time.fromisoformat(start), TZ)
        if starts_at <= now():
            raise StoreError("slot_in_past", "That time has already passed.")
        return slot_id

    def cancel(self, appointment_id: str) -> dict:
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE appointments SET status = 'cancelled', cancelled_at = ? WHERE id = ? AND status = 'booked'",
                (now().isoformat(timespec="seconds"), appointment_id),
            )
            if cur.rowcount == 0:
                raise StoreError("not_found", "No active appointment with that reference.")
        return self.appointment(appointment_id)  # type: ignore[return-value]

    def _insert_appointment(self, slot_id: str, phone: str, name: str | None, source: str, reason: str | None = None) -> str:
        appointment_id = "APT-" + uuid.uuid4().hex[:6].upper()
        self._db.execute(
            "INSERT INTO appointments (id, slot_id, phone, patient_name, reason, status, source, created_at) VALUES (?, ?, ?, ?, ?, 'booked', ?, ?)",
            (appointment_id, slot_id, phone, name, reason, source, now().isoformat(timespec="seconds")),
        )
        return appointment_id


def _slot_id(doctor_id: str, day: date, start: str) -> str:
    return f"{doctor_id}-{day.isoformat()}-{start.replace(':', '')}"


def _simplify(text: str) -> str:
    return re.sub(r"[^a-z ]", " ", text.lower()).strip()
