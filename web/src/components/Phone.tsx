import { useEffect, useRef, useState } from "react";

import { type Appointment, relativeDay, time12, todayIST } from "../lib/api";
import type { CallApi, Line } from "../lib/useCall";
import { Battery, Check, Cross, Leaf, Mic, MicOff, PhoneDown, Signal, Speaker, SpeakerLow } from "./icons";
import { Waveform } from "./Waveform";

function useClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 15_000);
    return () => window.clearInterval(id);
  }, []);
  return now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" });
}

function useElapsed(startedAt: number | null) {
  const [, tick] = useState(0);
  useEffect(() => {
    if (!startedAt) return;
    const id = window.setInterval(() => tick((n) => n + 1), 500);
    return () => window.clearInterval(id);
  }, [startedAt]);
  return startedAt ? Math.floor((Date.now() - startedAt) / 1000) : 0;
}

export const fmtDuration = (s: number) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;

function Ticket({ action, a }: { action: "booked" | "cancelled"; a: Appointment }) {
  const t = time12(a.start);
  return (
    <div className={`ticket ${action}`} role="status">
      <div className="ticket-top">
        <span className="badge">
          {action === "booked" ? <Check size={13} /> : <Cross size={13} />}
          {action === "booked" ? "Appointment booked" : "Appointment cancelled"}
        </span>
        <span className="ref">{a.id}</span>
      </div>
      <div className="ticket-when">
        <span className="big">
          {t.clock}
          <small>{t.suffix}</small>
        </span>
        <span className="day">{relativeDay(a.day, todayIST())}</span>
      </div>
      <div className="ticket-who">
        <span>{a.doctor_name}</span>
        <span>
          {a.specialty} · Room {a.room}
        </span>
      </div>
    </div>
  );
}

function Transcript({ lines }: { lines: Line[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [lines]);

  return (
    <div className="transcript" ref={ref} aria-live="polite">
      {lines.length === 0 && <p className="hint">Say hello. Ask for a doctor, a day, or to cancel.</p>}
      {lines.map((l) =>
        l.kind === "card" ? (
          <Ticket key={l.id} action={l.action} a={l.appointment} />
        ) : (
          <div key={l.id} className={`bubble ${l.kind} ${l.kind === "caller" && !l.final ? "interim" : ""}`}>
            {l.text}
          </div>
        ),
      )}
    </div>
  );
}

export function PhoneCall({ call, onClose }: { call: CallApi; onClose: () => void }) {
  const clock = useClock();
  const elapsed = useElapsed(call.state === "connected" ? call.startedAt : null);
  const { state } = call;
  const live = state === "connected";
  const booked = call.lines.filter((l) => l.kind === "card" && l.action === "booked").length;
  const cancelled = call.lines.filter((l) => l.kind === "card" && l.action === "cancelled").length;

  // Esc ends a live call, or closes the phone afterwards
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (state === "dialing" || state === "connected") call.hangUp();
      else onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [state, call, onClose]);

  return (
    <div className={`phone-wrap state-${state}`}>
      <div className="phone" role="dialog" aria-label="Call with Linden Clinic">
        <div className="screen">
          <div className="statusbar">
            <span>{clock}</span>
            <span className="island" aria-hidden>
              {live && <i className={`island-dot ${call.speaking ?? ""}`} />}
            </span>
            <span className="sb-icons">
              <Signal />
              <Battery />
            </span>
          </div>

          {(state === "dialing" || state === "failed") && (
            <div className="dial">
              <div className={`halo ${state === "dialing" ? "ringing" : ""}`}>
                <span className="ring r1" />
                <span className="ring r2" />
                <span className="ring r3" />
                <span className="avatar xl">
                  <Leaf size={46} />
                </span>
              </div>
              <h3 className="callee">Linden Clinic</h3>
              <p className="dial-sub">{state === "dialing" ? "Calling front desk…" : call.error}</p>
            </div>
          )}

          {(live || state === "ended") && (
            <div className="incall">
              <div className="incall-head">
                <span className="avatar sm">
                  <Leaf size={20} />
                </span>
                <span className="incall-who">
                  <strong>Linden Clinic</strong>
                  <span>Maya · front desk</span>
                </span>
                <span className={`timer ${live ? "" : "stopped"}`}>{fmtDuration(live ? elapsed : call.endedAfter)}</span>
              </div>

              {live ? (
                <Waveform local={call.analysers.local} remote={call.analysers.remote} muted={call.muted} />
              ) : (
                <div className="ended-banner">
                  <strong>Call ended</strong>
                  <span>
                    {[booked && `${booked} booked`, cancelled && `${cancelled} cancelled`].filter(Boolean).join(" · ") || "No changes"}
                  </span>
                </div>
              )}

              <Transcript lines={call.lines} />
            </div>
          )}

          <div className="controls">
            {live && (
              <>
                <button className={`ctl ${call.muted ? "on" : ""}`} onClick={call.toggleMute} aria-pressed={call.muted}>
                  <span className="ctl-ic">{call.muted ? <MicOff /> : <Mic />}</span>
                  <span className="ctl-l">{call.muted ? "Unmute" : "Mute"}</span>
                </button>
                <button className="ctl end" onClick={call.hangUp}>
                  <span className="ctl-ic">
                    <PhoneDown size={26} />
                  </span>
                  <span className="ctl-l">End</span>
                </button>
                <button className={`ctl ${call.speaker ? "" : "on"}`} onClick={call.toggleSpeaker} aria-pressed={!call.speaker}>
                  <span className="ctl-ic">{call.speaker ? <Speaker /> : <SpeakerLow />}</span>
                  <span className="ctl-l">{call.speaker ? "Speaker" : "Quiet"}</span>
                </button>
              </>
            )}
            {state === "dialing" && (
              <button className="ctl end" onClick={call.hangUp}>
                <span className="ctl-ic">
                  <PhoneDown size={26} />
                </span>
                <span className="ctl-l">Cancel</span>
              </button>
            )}
            {(state === "ended" || state === "failed") && (
              <div className="after">
                <button className="pill ghost" onClick={onClose}>
                  Close
                </button>
                <button className="pill go" onClick={() => void call.start()}>
                  Call again
                </button>
              </div>
            )}
          </div>
          <span className="home-bar" aria-hidden />
        </div>
      </div>
    </div>
  );
}
