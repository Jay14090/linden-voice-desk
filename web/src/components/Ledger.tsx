import { useMemo } from "react";

import { type Appointment, maskPhone, relativeDay, shortDoctor, time12 } from "../lib/api";
import { Wave } from "./icons";

type Props = {
  appointments: Appointment[];
  today: string;
  flashes: Map<string, "booked" | "cancelled">;
};

export function Ledger({ appointments, today, flashes }: Props) {
  const upcoming = useMemo(
    () => appointments.filter((a) => a.day >= today).sort((a, b) => (a.day + a.start).localeCompare(b.day + b.start)),
    [appointments, today],
  );
  const groups = useMemo(() => {
    const m = new Map<string, Appointment[]>();
    for (const a of upcoming) m.set(a.day, [...(m.get(a.day) ?? []), a]);
    return [...m.entries()];
  }, [upcoming]);

  const booked = upcoming.filter((a) => a.status === "booked").length;
  const viaVoice = upcoming.filter((a) => a.source === "voice" && a.status === "booked").length;

  return (
    <section className="card ledger" aria-label="Appointments">
      <header className="card-head">
        <div>
          <h2>Appointments</h2>
          <p className="sub">
            {booked} upcoming{viaVoice ? ` · ${viaVoice} by voice` : ""}
          </p>
        </div>
      </header>

      <div className="ledger-body">
        {groups.length === 0 && <p className="empty">Nothing booked yet.</p>}
        {groups.map(([day, list]) => (
          <div key={day} className="lgroup">
            <h3>{relativeDay(day, today)}</h3>
            <ul>
              {list.map((a) => {
                const t = time12(a.start);
                const flash = flashes.get(a.id);
                return (
                  <li key={a.id} className={`appt ${a.status} ${flash ? `flash-${flash}` : ""}`}>
                    <span className="at">
                      {t.clock}
                      <small>{t.suffix}</small>
                    </span>
                    <span className="pt">
                      <strong>{a.patient_name ?? "Unnamed caller"}</strong>
                      <span className="mono">{maskPhone(a.phone)}</span>
                    </span>
                    <span className="doc">
                      <span>{shortDoctor(a.doctor_name)}</span>
                      <small>{a.status === "cancelled" ? "Cancelled" : `Rm ${a.room}`}</small>
                    </span>
                    {a.source === "voice" && (
                      <span className="via" title="Booked over a voice call">
                        <Wave size={13} />
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}
