import { useEffect, useRef, useState } from "react";

import { relativeDay, shortDoctor, time12, todayIST } from "../lib/api";
import type { CallApi, Line } from "../lib/useCall";
import { Battery, Check, Cross, Leaf, Mic, MicOff, PhoneDown, Signal, Speaker, SpeakerLow } from "./icons";

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

/** Avatar with rings that swell with whoever is talking: ivory for the agent, mint for the caller. */
function VoiceAvatar({ call }: { call: CallApi }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const buf = new Uint8Array(256);
    let raf = 0;
    let smooth = 0;
    const level = (an: AnalyserNode | null) => {
      if (!an) return 0;
      an.getByteTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) {
        const v = (buf[i] - 128) / 128;
        sum += v * v;
      }
      return Math.sqrt(sum / buf.length);
    };
    const draw = () => {
      const agent = level(call.analysers.remote.current);
      const caller = call.muted ? 0 : level(call.analysers.local.current);
      const v = Math.min(1, Math.max(agent, caller) * 4);
      smooth += (v - smooth) * 0.25;
      const el = ref.current;
      if (el) {
        el.style.setProperty("--lvl", smooth.toFixed(3));
        el.dataset.who = agent > caller ? "agent" : caller > 0.02 ? "caller" : "";
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [call.analysers, call.muted]);

  return (
    <div className="voice-avatar" ref={ref}>
      <span className="va-ring va-r1" />
      <span className="va-ring va-r2" />
      <span className="avatar xl">
        <Leaf size={46} />
      </span>
    </div>
  );
}

/** Phone-style notification that drops in when the agent books or cancels. */
function Notice({ lines }: { lines: Line[] }) {
  const last = [...lines].reverse().find((l) => l.kind === "card");
  const [shown, setShown] = useState<string | null>(null);
  useEffect(() => {
    if (!last) return;
    setShown(last.id);
    const id = window.setTimeout(() => setShown(null), 6000);
    return () => window.clearTimeout(id);
  }, [last?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!last || last.kind !== "card" || shown !== last.id) return null;
  const a = last.appointment;
  const t = time12(a.start);
  return (
    <div className={`notice ${last.action}`} role="status">
      <span className="notice-ic">{last.action === "booked" ? <Check size={15} /> : <Cross size={15} />}</span>
      <span className="notice-body">
        <strong>{last.action === "booked" ? "Appointment booked" : "Appointment cancelled"}</strong>
        <span>
          {shortDoctor(a.doctor_name)} · {relativeDay(a.day, todayIST())}, {t.clock} {t.suffix} · Room {a.room}
        </span>
      </span>
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

  const status =
    state === "dialing"
      ? "calling…"
      : live
        ? fmtDuration(elapsed)
        : state === "ended"
          ? `Call ended · ${fmtDuration(call.endedAfter)}`
          : call.error;

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

          {live && <Notice lines={call.lines} />}

          <div className="call-head">
            <h3 className="callee">Linden Clinic</h3>
            <p className={`call-status ${live ? "live" : ""}`}>{status}</p>
          </div>

          <div className="call-stage">
            {live ? (
              <VoiceAvatar call={call} />
            ) : (
              <div className={`halo ${state === "dialing" ? "ringing" : ""}`}>
                <span className="ring r1" />
                <span className="ring r2" />
                <span className="ring r3" />
                <span className="avatar xl">
                  <Leaf size={46} />
                </span>
              </div>
            )}
            {state === "ended" && (
              <p className="call-summary">
                {[booked && `${booked} booked`, cancelled && `${cancelled} cancelled`].filter(Boolean).join(" · ") || "No changes made"}
              </p>
            )}
          </div>

          <div className="controls">
            {live && (
              <>
                <div className="ctl-row">
                  <button className={`ctl ${call.muted ? "on" : ""}`} onClick={call.toggleMute} aria-pressed={call.muted}>
                    <span className="ctl-ic">{call.muted ? <MicOff /> : <Mic />}</span>
                    <span className="ctl-l">{call.muted ? "unmute" : "mute"}</span>
                  </button>
                  <button className={`ctl ${call.speaker ? "on" : ""}`} onClick={call.toggleSpeaker} aria-pressed={call.speaker}>
                    <span className="ctl-ic">{call.speaker ? <Speaker /> : <SpeakerLow />}</span>
                    <span className="ctl-l">speaker</span>
                  </button>
                </div>
                <button className="ctl end" onClick={call.hangUp} aria-label="End call">
                  <span className="ctl-ic">
                    <PhoneDown size={28} />
                  </span>
                </button>
              </>
            )}
            {state === "dialing" && (
              <button className="ctl end" onClick={call.hangUp} aria-label="Cancel call">
                <span className="ctl-ic">
                  <PhoneDown size={28} />
                </span>
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
