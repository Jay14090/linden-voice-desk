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

  const doc = doctors.find((d) => d.id === doctorId);

  // days this doctor works, and the free times that day (plus the booking's own slot)
  const days = useMemo(() => [...new Set((doc?.slots ?? []).filter((s) => !s.past).map((s) => s.day))], [doc]);
  const times = useMemo(() => {
    const own = appt && appt.doctor_id === doctorId && appt.day === day ? appt.start : null;
    return (doc?.slots ?? []).filter((s) => s.day === day && !s.past && (s.free || s.start === own)).map((s) => s.start);
  }, [doc, day, appt, doctorId]);

  useEffect(() => {
    if (!days.includes(day)) setDay(days[0] ?? "");
  }, [days]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!times.includes(start)) setStart(times[0] ?? "");
  }, [times]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

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
    if (creating) {
      return run(() => api.create({ doctor_id: doctorId, day, start, phone, patient_name: name, reason }), true);
    }
    const patch: AppointmentPatch = {};
    if (name !== (appt.patient_name ?? "")) patch.patient_name = name;
    if (phone.replace(/\D/g, "") !== appt.phone) patch.phone = phone;
    if (reason !== (appt.reason ?? "")) patch.reason = reason;
    if (doctorId !== appt.doctor_id || day !== appt.day || start !== appt.start) Object.assign(patch, { doctor_id: doctorId, day, start });
    if (Object.keys(patch).length === 0) return onClose();
    return run(() => api.update(appt.id, patch));
  };

  const cancelled = appt?.status === "cancelled";

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
              </p>
            )}
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <Cross size={18} />
          </button>
        </header>

        <div className="drawer-body">
          <label className="field">
            <span>Patient name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Full name" />
          </label>
          <label className="field">
            <span>Mobile number</span>
            <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="98765 43210" inputMode="tel" className="mono" />
          </label>
          <label className="field">
            <span>Problem</span>
            <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} placeholder="What the patient is coming in for" />
          </label>

          <div className="field-group">
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
                  {days.length === 0 && <option>No days this week</option>}
                  {days.map((d) => {
                    const p = dayParts(d);
                    return (
                      <option key={d} value={d}>
                        {d === today ? "Today" : `${p.weekday} ${p.date} ${p.month}`}
                      </option>
                    );
                  })}
                </select>
              </label>
              <label className="field">
                <span>Time</span>
                <select value={start} onChange={(e) => setStart(e.target.value)} disabled={!times.length}>
                  {times.length === 0 && <option>Fully booked</option>}
                  {times.map((t) => {
                    const p = time12(t);
                    return (
                      <option key={t} value={t}>
                        {p.clock} {p.suffix}
                      </option>
                    );
                  })}
                </select>
              </label>
            </div>
          </div>

          {error && <p className="form-error">{error}</p>}
          {saved && <p className="form-ok">Saved.</p>}
        </div>

        <footer className="drawer-foot">
          {!creating && (
            <button
              className={cancelled ? "btn-ghost" : "btn-danger"}
              disabled={busy}
              onClick={() => run(() => api.update(appt.id, { status: cancelled ? "booked" : "cancelled" }))}
            >
              {cancelled ? "Restore booking" : "Cancel appointment"}
            </button>
          )}
          <span className="grow" />
          <button className="btn-ghost" onClick={onClose}>
            Close
          </button>
          <button className="btn-primary" disabled={busy || !start || !phone || cancelled} onClick={() => void save()}>
            {creating ? "Book" : "Save changes"}
          </button>
        </footer>
      </aside>
    </>
  );
}
