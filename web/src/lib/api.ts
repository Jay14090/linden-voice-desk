export type Slot = { day: string; start: string; free: boolean; past?: boolean };

export type Doctor = {
  id: string;
  name: string;
  specialty: string;
  room: string;
  slots: Slot[];
};

export type Appointment = {
  id: string;
  slot_id: string;
  phone: string;
  patient_name: string | null;
  reason: string | null;
  status: "booked" | "cancelled";
  source: "voice" | "seed" | "desk";
  created_at: string;
  cancelled_at: string | null;
  day: string;
  start: string;
  doctor_id: string;
  doctor_name: string;
  specialty: string;
  room: string;
};

export type ToolEvent = {
  session: string;
  id: string;
  name: "check_doctor_availability" | "book_appointment" | "cancel_appointment";
  args: Record<string, unknown>;
  state: "running" | "done";
  ok?: boolean;
  error?: string | null;
  ms?: number;
};

export type AppointmentEvent = {
  session: string;
  kind: "booked" | "cancelled" | "updated" | "rescheduled";
  appointment: Appointment;
};

export type CallEvent = {
  session: string;
  state: "connecting" | "connected" | "ended";
  seconds?: number;
};

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} ${res.status}`);
  return res.json() as Promise<T>;
}

export type AppointmentPatch = Partial<{
  patient_name: string;
  phone: string;
  reason: string;
  doctor_id: string;
  day: string;
  start: string;
  status: "booked" | "cancelled";
}>;

export type AppointmentCreate = {
  doctor_id: string;
  day: string;
  start: string;
  phone: string;
  patient_name?: string;
  reason?: string;
};

async function send<T>(method: string, path: string, body: unknown): Promise<T> {
  const res = await fetch(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(typeof detail?.detail === "string" ? detail.detail : `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  doctors: () => get<Doctor[]>("/api/doctors"),
  appointments: () => get<Appointment[]>("/api/appointments"),
  update: (id: string, patch: AppointmentPatch) => send<Appointment>("PATCH", `/api/appointments/${id}`, patch),
  create: (body: AppointmentCreate) => send<Appointment>("POST", "/api/appointments", body),
  health: () => get<{ ok: boolean; llm: string; keys: Record<string, boolean> }>("/api/health"),
  reset: () => fetch("/api/reset", { method: "POST" }),
};

export type ServerEvents = {
  appointment: AppointmentEvent;
  tool: ToolEvent;
  call: CallEvent;
  reset: Record<string, never>;
};

/** Subscribe to the server's SSE stream. Returns an unsubscribe function. */
export function subscribe(
  handlers: { [K in keyof ServerEvents]?: (data: ServerEvents[K]) => void } & {
    open?: () => void;
    error?: () => void;
  },
): () => void {
  const es = new EventSource("/api/events");
  (Object.keys(handlers) as (keyof typeof handlers)[]).forEach((name) => {
    if (name === "open" || name === "error") return;
    es.addEventListener(name, (e) => {
      const fn = handlers[name] as ((d: unknown) => void) | undefined;
      fn?.(JSON.parse((e as MessageEvent).data));
    });
  });
  es.onopen = () => handlers.open?.();
  es.onerror = () => handlers.error?.();
  return () => es.close();
}

// ---------- formatting ----------

export const slotKey = (doctorId: string, day: string, start: string) => `${doctorId}|${day}|${start}`;

export function toMinutes(hhmm: string) {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

export function time12(hhmm: string) {
  const [h, m] = hhmm.split(":").map(Number);
  const suffix = h < 12 ? "AM" : "PM";
  return { clock: `${h % 12 || 12}:${String(m).padStart(2, "0")}`, suffix };
}

export function dayParts(iso: string) {
  const d = new Date(`${iso}T00:00:00`);
  return {
    weekday: d.toLocaleDateString("en-IN", { weekday: "short" }),
    longWeekday: d.toLocaleDateString("en-IN", { weekday: "long" }),
    date: d.getDate(),
    month: d.toLocaleDateString("en-IN", { month: "short" }),
  };
}

export function relativeDay(iso: string, today: string) {
  const diff = Math.round((+new Date(`${iso}T00:00:00`) - +new Date(`${today}T00:00:00`)) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  const p = dayParts(iso);
  return `${p.longWeekday}, ${p.date} ${p.month}`;
}

export function maskPhone(digits: string) {
  return `${digits.slice(0, 5)} ${digits.slice(5, 6)}••${digits.slice(8)}`;
}

export function formatPhone(digits: string) {
  return `${digits.slice(0, 5)} ${digits.slice(5)}`;
}

export function shortDoctor(name: string) {
  const parts = name.replace("Dr. ", "").split(" ");
  return `Dr. ${parts[parts.length - 1]}`;
}

export function todayIST() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
}

export function nowMinutesIST() {
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date());
  return toMinutes(parts);
}
