import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Activity, type ActivityItem } from "./components/Activity";
import { Leaf, Phone, Reset } from "./components/icons";
import { Ledger } from "./components/Ledger";
import { fmtDuration, PhoneCall } from "./components/Phone";
import { Schedule } from "./components/Schedule";
import { type Appointment, type Doctor, type ToolEvent, api, nowMinutesIST, slotKey, subscribe, todayIST } from "./lib/api";
import { useCall } from "./lib/useCall";

const FLASH_MS = 4200;

function useTicker(ms: number) {
  const [, set] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => set((n) => n + 1), ms);
    return () => window.clearInterval(id);
  }, [ms]);
}

export default function App() {
  const [doctors, setDoctors] = useState<Doctor[]>([]);
  const [appointments, setAppointments] = useState<Appointment[]>([]);
  const [day, setDay] = useState(todayIST());
  const [flashes, setFlashes] = useState<Map<string, "booked" | "cancelled">>(new Map());
  const [activeDoctors, setActiveDoctors] = useState<Set<string>>(new Set());
  const [activity, setActivity] = useState<ActivityItem[]>([]);
  const [phoneOpen, setPhoneOpen] = useState(false);
  const [online, setOnline] = useState(true);
  const call = useCall();
  const seq = useRef(0);
  useTicker(30_000);

  const today = todayIST();
  const nowMin = nowMinutesIST();

  const refresh = useCallback(async () => {
    const [d, a] = await Promise.all([api.doctors(), api.appointments()]);
    setDoctors(d);
    setAppointments(a);
  }, []);

  const flash = useCallback((keys: string[], kind: "booked" | "cancelled") => {
    setFlashes((prev) => {
      const next = new Map(prev);
      keys.forEach((k) => next.set(k, kind));
      return next;
    });
    window.setTimeout(() => {
      setFlashes((prev) => {
        const next = new Map(prev);
        keys.forEach((k) => next.delete(k));
        return next;
      });
    }, FLASH_MS);
  }, []);

  const log = useCallback((item: { kind: "tool"; tool: ToolEvent } | { kind: "call"; text: string }) => {
    setActivity((prev) => [{ ...item, id: `a${++seq.current}`, at: new Date() } as ActivityItem, ...prev].slice(0, 60));
  }, []);

  useEffect(() => {
    void refresh().catch(() => setOnline(false));
    return subscribe({
      open: () => {
        setOnline(true);
        void refresh();
      },
      error: () => setOnline(false),
      appointment: (e) => {
        void refresh();
        const a = e.appointment;
        setDay(a.day); // bring the change into view
        flash([a.id, slotKey(a.doctor_id, a.day, a.start)], e.kind);
      },
      tool: (t) => {
        if (t.state === "running") {
          log({ kind: "tool", tool: t });
          const doc = typeof t.args.doctor_id === "string" ? t.args.doctor_id : null;
          if (doc && t.name === "check_doctor_availability") {
            setActiveDoctors(new Set([doc]));
            window.setTimeout(() => setActiveDoctors(new Set()), 2600);
            const when = typeof t.args.date === "string" ? t.args.date : null;
            if (when) setDay(when);
          }
        } else {
          setActivity((prev) =>
            prev.map((it) => (it.kind === "tool" && it.tool.id === t.id ? { ...it, tool: t } : it)),
          );
        }
      },
      call: (c) => {
        if (c.state === "connected") log({ kind: "call", text: "Call connected" });
        if (c.state === "ended") log({ kind: "call", text: `Call ended · ${fmtDuration(c.seconds ?? 0)}` });
      },
      reset: () => void refresh(),
    });
  }, [refresh, flash, log]);

  const days = useMemo(() => {
    const out: string[] = [];
    const base = new Date(`${today}T00:00:00`);
    for (let i = 0; i < 7; i++) {
      const d = new Date(base);
      d.setDate(base.getDate() + i);
      out.push(d.toLocaleDateString("en-CA"));
    }
    return out;
  }, [today]);

  const openPhone = () => {
    setPhoneOpen(true);
    void call.start();
  };
  const closePhone = () => {
    setPhoneOpen(false);
    call.reset();
  };

  const onCall = call.state === "dialing" || call.state === "connected";
  const dateLabel = new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", timeZone: "Asia/Kolkata" });

  return (
    <div className={`app ${phoneOpen ? "with-phone" : ""}`}>
      <header className="topbar">
        <div className="brand">
          <span className="mark">
            <Leaf size={22} />
          </span>
          <span className="brand-name">Linden</span>
          <span className="brand-div" />
          <span className="brand-sub">Front desk</span>
        </div>

        <div className="top-right">
          <span className={`agent-chip ${onCall ? "busy" : online ? "" : "down"}`}>
            <i />
            {!online ? "Server offline" : onCall ? "Maya is on a call" : "Maya is ready"}
          </span>
          <button className="reset" onClick={() => void api.reset()} title="Reset demo data" aria-label="Reset demo data">
            <Reset size={16} />
          </button>
          <button className={`try ${onCall ? "live" : ""}`} onClick={onCall ? call.hangUp : openPhone} disabled={!online && !onCall}>
            <Phone size={17} />
            {call.state === "connected" ? "End call" : call.state === "dialing" ? "Calling…" : "Try call"}
          </button>
        </div>
      </header>

      <main className="layout">
        <div className="intro">
          <h1>{dateLabel}</h1>
          <p>Callers book, check and cancel by voice. Everything lands here.</p>
        </div>

        <div className="main-col">
          <Schedule
            doctors={doctors}
            appointments={appointments}
            days={days}
            day={day}
            onDay={setDay}
            today={today}
            nowMin={nowMin}
            flashes={flashes}
            activeDoctors={activeDoctors}
          />
          <Activity items={activity} />
        </div>

        <aside className="side-col">
          <Ledger appointments={appointments} today={today} flashes={flashes} />
        </aside>
      </main>

      {phoneOpen && <PhoneCall call={call} onClose={closePhone} />}
    </div>
  );
}
