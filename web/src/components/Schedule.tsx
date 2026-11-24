import { useMemo } from "react";

import { type Appointment, type Doctor, dayParts, slotKey, time12, toMinutes } from "../lib/api";

type Props = {
  doctors: Doctor[];
  appointments: Appointment[];
  days: string[];
  day: string;
  onDay: (d: string) => void;
  today: string;
  nowMin: number;
  flashes: Map<string, "booked" | "cancelled">;
  activeDoctors: Set<string>;
};

const SLOT = 30;

const SPECIALTY_HUE: Record<string, string> = {
  "General Physician": "var(--hue-1)",
  Cardiologist: "var(--hue-2)",
  Dermatologist: "var(--hue-3)",
  Pediatrician: "var(--hue-4)",
  "Orthopedic Surgeon": "var(--hue-5)",
  "ENT Specialist": "var(--hue-6)",
};

function initials(name: string | null) {
  if (!name) return "•";
  const p = name.trim().split(/\s+/);
  return (p[0][0] + (p[1]?.[0] ?? "")).toUpperCase();
}

export function Schedule({ doctors, appointments, days, day, onDay, today, nowMin, flashes, activeDoctors }: Props) {
  const booking = useMemo(() => {
    const m = new Map<string, Appointment>();
    for (const a of appointments) if (a.status === "booked") m.set(slotKey(a.doctor_id, a.day, a.start), a);
    return m;
  }, [appointments]);

  const freeByDay = useMemo(() => {
    const m = new Map<string, number>();
    for (const d of doctors) for (const s of d.slots) m.set(s.day, (m.get(s.day) ?? 0) + (s.free ? 1 : 0));
    return m;
  }, [doctors]);

  // Axis for the selected day: earliest shift start to latest shift end, on whole hours.
  const { start, end } = useMemo(() => {
    let lo = 24 * 60;
    let hi = 0;
    for (const d of doctors)
      for (const s of d.slots)
        if (s.day === day) {
          lo = Math.min(lo, toMinutes(s.start));
          hi = Math.max(hi, toMinutes(s.start) + SLOT);
        }
    if (lo > hi) return { start: 9 * 60, end: 19 * 60 };
    return { start: Math.floor(lo / 60) * 60, end: Math.ceil(hi / 60) * 60 };
  }, [doctors, day]);

  const span = end - start;
  const pct = (m: number) => `${((m - start) / span) * 100}%`;
  const hours = Array.from({ length: span / 60 + 1 }, (_, i) => start + i * 60);
  const isToday = day === today;
  const nowInRange = isToday && nowMin > start && nowMin < end;

  return (
    <section className="card schedule" aria-label="Doctors and slots">
      <header className="card-head">
        <div>
          <h2>Schedule</h2>
          <p className="sub">30-minute slots, updated live</p>
        </div>
        <div className="legend" aria-hidden>
          <span><i className="lg free" /> Open</span>
          <span><i className="lg taken" /> Booked</span>
        </div>
      </header>

      <nav className="days" aria-label="Choose day">
        {days.map((d) => {
          const p = dayParts(d);
          const open = freeByDay.get(d);
          return (
            <button key={d} className={`day ${d === day ? "on" : ""}`} onClick={() => onDay(d)} aria-pressed={d === day}>
              <span className="wd">{d === today ? "Today" : p.weekday}</span>
              <span className="dt">{p.date}</span>
              <span className="ct">{open === undefined ? "Closed" : open ? `${open} open` : "Full"}</span>
            </button>
          );
        })}
      </nav>

      <div className="board-scroll">
        <div className="board">
          <div className="axis">
            <div className="who" />
            <div className="track">
              {hours.map((h) => (
                <span key={h} className="tick" style={{ left: pct(h) }}>
                  {time12(`${Math.floor(h / 60)}:00`).clock.replace(":00", "")}
                  <small>{h < 720 ? "am" : "pm"}</small>
                </span>
              ))}
            </div>
            <div className="count" />
          </div>

          {doctors.map((doc) => {
            const slots = doc.slots.filter((s) => s.day === day);
            const open = slots.filter((s) => s.free).length;
            const allPast = slots.length > 0 && slots.every((s) => s.past);
            const off = slots.length === 0;
            return (
              <div key={doc.id} className={`row ${activeDoctors.has(doc.id) ? "looking" : ""}`}>
                <div className="who">
                  <span className="mono-avatar" style={{ ["--hue" as string]: SPECIALTY_HUE[doc.specialty] }}>
                    {doc.name.replace("Dr. ", "").split(" ").map((w) => w[0]).join("")}
                  </span>
                  <span className="who-text">
                    <strong>{doc.name}</strong>
                    <span>{doc.specialty} · Rm {doc.room}</span>
                  </span>
                </div>

                <div className="track">
                  {hours.map((h) => (
                    <span key={h} className="grid" style={{ left: pct(h) }} />
                  ))}
                  {isToday && nowMin > start && (
                    <span className="past" style={{ width: pct(Math.min(nowMin, end)) }} />
                  )}
                  {off && <span className="off">{freeByDay.get(day) === undefined ? "Clinic closed" : "Not in clinic"}</span>}
                  {slots.map((s) => {
                    const m = toMinutes(s.start);
                    const key = slotKey(doc.id, s.day, s.start);
                    const appt = booking.get(key);
                    const flash = flashes.get(key);
                    const t = time12(s.start);
                    const label = s.past
                      ? `${doc.name}, ${t.clock} ${t.suffix}: past`
                      : s.free
                      ? `${doc.name}, ${t.clock} ${t.suffix}: open`
                      : `${doc.name}, ${t.clock} ${t.suffix}: booked${appt?.patient_name ? ` for ${appt.patient_name}` : ""}`;
                    return (
                      <span
                        key={key}
                        className={`blk ${s.past ? (appt ? "taken past-slot" : "gone") : s.free ? "free" : "taken"} ${flash ? `flash-${flash}` : ""}`}
                        style={{ left: pct(m), width: `calc(${(SLOT / span) * 100}% - 3px)` }}
                        title={label}
                        aria-label={label}
                      >
                        {!s.free && <em>{initials(appt?.patient_name ?? null)}</em>}
                        <span className="tip">
                          {t.clock}
                          <small>{t.suffix}</small>
                        </span>
                      </span>
                    );
                  })}
                  {nowInRange && <span className="now" style={{ left: pct(nowMin) }} />}
                </div>

                <div className="count">
                  {off ? <span className="muted">—</span> : allPast ? <span className="muted done">Done</span> : <><b>{open}</b><span>open</span></>}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
