import { useEffect, useMemo, useState } from "react";

import {
  type Appointment,
  type AppointmentPatch,
  type Doctor,
  api,
  dayParts,
  formatPhone,
  relativeDay,
  shortDoctor,
  time12,
} from "../lib/api";
import { Cross, Wave } from "./icons";

type StatusFilter = "upcoming" | "past" | "cancelled" | "all";

type Props = {
  doctors: Doctor[];
  appointments: Appointment[];
  today: string;
  nowMin: number;
  flashes: Map<string, "booked" | "cancelled">;
};

const SOURCE_LABEL: Record<Appointment["source"], string> = { voice: "Voice", desk: "Desk", seed: "Existing" };

function isPast(a: Appointment, today: string, nowMin: number) {
  if (a.day !== today) return a.day < today;
  const [h, m] = a.start.split(":").map(Number);
  return h * 60 + m <= nowMin;
}

export function Bookings({ doctors, appointments, today, nowMin, flashes }: Props) {
  const [doctor, setDoctor] = useState<string>("all");
  const [status, setStatus] = useState<StatusFilter>("upcoming");
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<Appointment | "new" | null>(null);

  const counts = useMemo(() => {
    const m = new Map<string, number>();
    for (const a of appointments) {
      if (a.status !== "booked" || isPast(a, today, nowMin)) continue;
      m.set(a.doctor_id, (m.get(a.doctor_id) ?? 0) + 1);
      m.set("all", (m.get("all") ?? 0) + 1);
    }
    return m;
  }, [appointments, today, nowMin]);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const qDigits = q.replace(/\D/g, "");
    return appointments
      .filter((a) => doctor === "all" || a.doctor_id === doctor)
      .filter((a) => {
        const past = isPast(a, today, nowMin);
        if (status === "upcoming") return a.status === "booked" && !past;
        if (status === "past") return a.status === "booked" && past;
        if (status === "cancelled") return a.status === "cancelled";
        return true;
      })
      .filter((a) => {
        if (!q) return true;
        return (
          (a.patient_name ?? "").toLowerCase().includes(q) ||
          (qDigits.length >= 3 && a.phone.includes(qDigits)) ||
          (a.reason ?? "").toLowerCase().includes(q) ||
          a.id.toLowerCase().includes(q)
        );
      })
      .sort((a, b) => {
        const k = (a.day + a.start).localeCompare(b.day + b.start);
        return status === "past" ? -k : k;
      });
  }, [appointments, doctor, status, query, today, nowMin]);

  // keep an open editor in sync with live changes (e.g. the agent cancels it mid-edit)
  useEffect(() => {
    if (editing && editing !== "new") {
      const fresh = appointments.find((a) => a.id === editing.id);
      if (fresh && fresh !== editing) setEditing(fresh);
    }
  }, [appointments]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="bookings">
      <aside className="card doc-filter" aria-label="Filter by doctor">
        <button className={`df ${doctor === "all" ? "on" : ""}`} onClick={() => setDoctor("all")}>
          <span className="df-text">
            <strong>All doctors</strong>
            <span>Every booking</span>
          </span>
          <b>{counts.get("all") ?? 0}</b>
        </button>
        {doctors.map((d) => (
          <button key={d.id} className={`df ${doctor === d.id ? "on" : ""}`} onClick={() => setDoctor(d.id)}>
            <span className="df-text">
              <strong>{d.name}</strong>
              <span>{d.specialty}</span>
            </span>
            <b>{counts.get(d.id) ?? 0}</b>
          </button>
        ))}
      </aside>

      <section className="card bk-main">
        <div className="bk-toolbar">
          <input
            className="search"
            type="search"
            placeholder="Search patient, phone or problem"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search bookings"
          />
          <div className="seg" role="tablist" aria-label="Status">
            {(["upcoming", "past", "cancelled", "all"] as StatusFilter[]).map((s) => (
              <button key={s} role="tab" aria-selected={status === s} className={status === s ? "on" : ""} onClick={() => setStatus(s)}>
                {s[0].toUpperCase() + s.slice(1)}
              </button>
            ))}
          </div>
          <button className="btn-primary" onClick={() => setEditing("new")}>
            New booking
          </button>
        </div>

        <div className="bk-table-wrap">
          <table className="bk-table">
            <thead>
              <tr>
                <th>When</th>
                <th>Patient</th>
                <th>Problem</th>
                <th>Doctor</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr>
                  <td colSpan={5} className="empty">
                    {query ? `No bookings match "${query}".` : "No bookings here."}
                  </td>
                </tr>
              )}
              {rows.map((a) => {
                const t = time12(a.start);
                const flash = flashes.get(a.id);
                return (
                  <tr key={a.id} className={`${a.status} ${flash ? `flash-${flash}` : ""}`} onClick={() => setEditing(a)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setEditing(a)}>
                    <td className="when">
                      <span className="mono">
                        {t.clock}
                        <small>{t.suffix}</small>
                      </span>
                      <span className="sub">{relativeDay(a.day, today)}</span>
                    </td>
                    <td>
                      <strong>{a.patient_name ?? "Unnamed caller"}</strong>
                      <span className="mono sub">{formatPhone(a.phone)}</span>
                    </td>
                    <td className="problem">{a.reason ?? <span className="muted">Not recorded</span>}</td>
                    <td>
                      <span>{shortDoctor(a.doctor_name)}</span>
                      <span className="sub">{a.specialty}</span>
                    </td>
                    <td>
                      <span className={`chip ${a.status}`}>{a.status === "booked" ? "Booked" : "Cancelled"}</span>
                      <span className="src">
                        {a.source === "voice" && <Wave size={12} />}
                        {SOURCE_LABEL[a.source]}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {editing && (
        <Editor
          key={editing === "new" ? "new" : editing.id}
          appt={editing === "new" ? null : editing}
          doctors={doctors}
          defaultDoctor={doctor === "all" ? doctors[0]?.id : doctor}
          today={today}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}

// ---------- edit / create drawer ----------

type EditorProps = {
  appt: Appointment | null;
  doctors: Doctor[];
  defaultDoctor?: string;
  today: string;
  onClose: () => void;
};

/** Same rules as the server: 10 digits, optionally +91 / 0 prefixed. */
function normalizePhone(raw: string): string | null {
  let d = raw.replace(/\D/g, "");
  if (d.length === 12 && d.startsWith("91")) d = d.slice(2);
  else if (d.length === 11 && d.startsWith("0")) d = d.slice(1);
  return d.length === 10 ? d : null;
}

type SlotOption = { start: string; state: "free" | "booked" | "past" | "own" };

function Editor({ appt, doctors, defaultDoctor, today, onClose }: EditorProps) {
  const creating = !appt;
  const [name, setName] = useState(appt?.patient_name ?? "");
  const [phone, setPhone] = useState(appt ? formatPhone(appt.phone) : "");
  const [reason, setReason] = useState(appt?.reason ?? "");
  const [doctorId, setDoctorId] = useState(appt?.doctor_id ?? defaultDoctor ?? "");
  const [day, setDay] = useState(appt?.day ?? "");
  const [start, setStart] = useState(appt?.start ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [touched, setTouched] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);

  const doc = doctors.find((d) => d.id === doctorId);
  const cancelled = appt?.status === "cancelled";
  const isOwn = (d: string, dayV: string, s: string) => !!appt && appt.doctor_id === d && appt.day === dayV && appt.start === s;

  // Days the doctor works from today on, plus the booking's own day even if it's past.
  const days = useMemo(() => {
    const set = new Set((doc?.slots ?? []).filter((s) => !s.past).map((s) => s.day));
    if (appt && appt.doctor_id === doctorId) set.add(appt.day);
    return [...set].sort();
  }, [doc, appt, doctorId]);

  // Every slot that day with its state, so booked times are visible (and unselectable).
  const slotOptions: SlotOption[] = useMemo(() => {
    const opts: SlotOption[] = (doc?.slots ?? [])
      .filter((s) => s.day === day)
      .map((s) => ({
        start: s.start,
        state: isOwn(doctorId, day, s.start) ? "own" : s.past ? "past" : s.free ? "free" : "booked",
      }));
    if (appt && isOwn(doctorId, day, appt.start) && !opts.some((o) => o.start === appt.start)) {
      opts.push({ start: appt.start, state: "own" });
    }
    return opts.filter((o) => o.state !== "past" || o.start === start).sort((a, b) => a.start.localeCompare(b.start));
  }, [doc, day, doctorId, appt, start]); // eslint-disable-line react-hooks/exhaustive-deps

  // Pick sensible defaults when the doctor or day changes, without ever silently
  // moving an existing booking off its own slot.
  useEffect(() => {
    if (day && days.includes(day)) return;
    setDay(days.find((d) => d >= today) ?? days[0] ?? "");
  }, [days]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (start && slotOptions.some((o) => o.start === start)) return;
    setStart(slotOptions.find((o) => o.state === "free" || o.state === "own")?.start ?? "");
  }, [day, doctorId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const chosen = slotOptions.find((o) => o.start === start);
  const moved = !creating && !!appt && !isOwn(doctorId, day, start);

  // ---- field checks (the server checks the same things) ----
  const errors: Record<string, string> = {};
  if (!name.trim()) errors.name = "Enter the patient's name.";
  else if (name.trim().length > 80) errors.name = "Keep the name under 80 characters.";
  if (!normalizePhone(phone)) errors.phone = "Enter a 10-digit mobile number.";
  if (reason.length > 300) errors.reason = "Keep the problem under 300 characters.";
  if (!cancelled) {
    if (!day) errors.slot = `${doc?.name ?? "This doctor"} has no clinic days left this week.`;
    else if (!start || !chosen) errors.slot = "Pick a time.";
    else if (chosen.state === "booked") errors.slot = "That time is already booked. Pick another slot.";
    else if (chosen.state === "past" && (creating || moved)) errors.slot = "That time has already passed.";
  }
  const valid = Object.keys(errors).length === 0;
  const show = (k: string) => (touched || saved || error) && errors[k];

  const run = async (fn: () => Promise<unknown>, close = false) => {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await fn();
      if (close) onClose();
      else setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const save = () => {
    setTouched(true);
    if (!valid || busy) return;
    if (creating) {
      return run(() => api.create({ doctor_id: doctorId, day, start, phone, patient_name: name.trim(), reason: reason.trim() || undefined }), true);
    }
    const patch: AppointmentPatch = {};
    if (name.trim() !== (appt.patient_name ?? "")) patch.patient_name = name.trim();
    if (normalizePhone(phone) !== appt.phone) patch.phone = phone;
    if (reason.trim() !== (appt.reason ?? "")) patch.reason = reason.trim();
    if (moved) Object.assign(patch, { doctor_id: doctorId, day, start });
    if (Object.keys(patch).length === 0) return onClose();
    return run(() => api.update(appt.id, patch));
  };

  const label = (o: SlotOption) => {
    const p = time12(o.start);
    const t = `${p.clock} ${p.suffix}`;
    return o.state === "booked" ? `${t} · booked` : o.state === "own" ? `${t} · current` : o.state === "past" ? `${t} · passed` : t;
  };

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label={creating ? "New booking" : "Edit booking"}>
        <header className="drawer-head">
          <div>
            <h2>{creating ? "New booking" : (appt.patient_name ?? "Unnamed caller")}</h2>
            {!creating && (
              <p className="sub mono">
                {appt.id} · booked {appt.source === "voice" ? "by voice" : appt.source === "desk" ? "at the desk" : "earlier"}
                {cancelled ? " · cancelled" : ""}
              </p>
            )}
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <Cross size={18} />
          </button>
        </header>

        <form
          className="drawer-body"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
          noValidate
        >
          <label className={`field ${show("name") ? "invalid" : ""}`}>
            <span>Patient name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Full name" maxLength={120} autoFocus={creating} />
            {show("name") && <em className="field-err">{errors.name}</em>}
          </label>
          <label className={`field ${show("phone") ? "invalid" : ""}`}>
            <span>Mobile number</span>
            <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="98765 43210" inputMode="tel" className="mono" maxLength={20} />
            {show("phone") && <em className="field-err">{errors.phone}</em>}
          </label>
          <label className={`field ${show("reason") ? "invalid" : ""}`}>
            <span>
              Problem <small className="count-hint">{reason.length}/300</small>
            </span>
            <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} placeholder="What the patient is coming in for" maxLength={400} />
            {show("reason") && <em className="field-err">{errors.reason}</em>}
          </label>

          <fieldset className={`field-group ${cancelled ? "locked" : ""}`} disabled={cancelled}>
            {cancelled && <p className="lock-note">This booking is cancelled. Restore it to change the time.</p>}
            <label className="field">
              <span>Doctor</span>
              <select value={doctorId} onChange={(e) => setDoctorId(e.target.value)}>
                {doctors.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}, {d.specialty}
                  </option>
                ))}
              </select>
            </label>
            <div className="field-row">
              <label className="field">
                <span>Day</span>
                <select value={day} onChange={(e) => setDay(e.target.value)} disabled={!days.length}>
                  {days.length === 0 && <option value="">No days this week</option>}
                  {days.map((d) => {
                    const p = dayParts(d);
                    return (
                      <option key={d} value={d}>
                        {d === today ? "Today" : `${p.weekday} ${p.date} ${p.month}`}
                        {d < today ? " · passed" : ""}
                      </option>
                    );
                  })}
                </select>
              </label>
              <label className="field">
                <span>Time</span>
                <select value={start} onChange={(e) => setStart(e.target.value)} disabled={!slotOptions.length}>
                  {slotOptions.length === 0 && <option value="">No slots</option>}
                  {slotOptions.map((o) => (
                    <option key={o.start} value={o.start} disabled={o.state === "booked" || (o.state === "past" && o.start !== start)}>
                      {label(o)}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {show("slot") || (chosen?.state === "booked" && errors.slot) ? <em className="field-err">{errors.slot}</em> : null}
          </fieldset>

          {error && <p className="form-error">{error}</p>}
          {saved && !error && <p className="form-ok">Saved.</p>}
          <button type="submit" hidden />
        </form>

        <footer className="drawer-foot">
          {!creating && (
            <button
              className={cancelled ? "btn-ghost" : "btn-danger"}
              disabled={busy}
              onClick={() => {
                if (!cancelled && !confirmCancel) return setConfirmCancel(true); // two-step, no browser popup
                setConfirmCancel(false);
                void run(() => api.update(appt.id, { status: cancelled ? "booked" : "cancelled" }));
              }}
              onBlur={() => setConfirmCancel(false)}
            >
              {cancelled ? "Restore booking" : confirmCancel ? `Yes, cancel with ${shortDoctor(appt.doctor_name)}` : "Cancel appointment"}
            </button>
          )}
          <span className="grow" />
          <button className="btn-ghost" onClick={onClose}>
            Close
          </button>
          <button className="btn-primary" disabled={busy || cancelled || (touched && !valid) || chosen?.state === "booked"} onClick={() => void save()}>
            {busy ? "Saving…" : creating ? "Book" : "Save changes"}
          </button>
        </footer>
      </aside>
    </>
  );
}
