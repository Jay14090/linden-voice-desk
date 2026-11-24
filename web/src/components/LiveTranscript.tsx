import { useEffect, useRef } from "react";

import { shortDoctor, time12 } from "../lib/api";
import type { CallState, Line } from "../lib/useCall";

type Props = { lines: Line[]; state: CallState };

export function LiveTranscript({ lines, state }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [lines]);

  const live = state === "connected" || state === "dialing";

  return (
    <section className="card transcript-card" aria-label="Live transcript">
      <header className="card-head">
        <div>
          <h2>Transcript</h2>
          <p className="sub">{live ? "Live" : lines.length ? "Last call" : "Appears during a call"}</p>
        </div>
        {live && <span className="live-dot" aria-hidden />}
      </header>
      <div className="tx" ref={ref} aria-live="polite">
        {lines.length === 0 && <p className="empty">Nothing yet. Start a call and the conversation shows up here.</p>}
        {lines.map((l) => {
          if (l.kind === "card") {
            const t = time12(l.appointment.start);
            return (
              <p key={l.id} className={`tx-event ${l.action}`}>
                {l.action === "booked" ? "Booked" : l.action === "rescheduled" ? "Moved to" : "Cancelled"} · {shortDoctor(l.appointment.doctor_name)} · {t.clock} {t.suffix}
              </p>
            );
          }
          return (
            <div key={l.id} className={`tx-line ${l.kind} ${l.kind === "caller" && !l.final ? "interim" : ""}`}>
              <span className="tx-who">{l.kind === "agent" ? "Maya" : "Caller"}</span>
              <span className="tx-text">{l.text}</span>
            </div>
          );
        })}
      </div>
    </section>
  );
}
