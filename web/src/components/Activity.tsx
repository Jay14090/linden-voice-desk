import type { ToolEvent } from "../lib/api";

export type ActivityItem =
  | { id: string; at: Date; kind: "tool"; tool: ToolEvent }
  | { id: string; at: Date; kind: "call"; text: string };

const LABEL: Record<ToolEvent["name"], string> = {
  check_doctor_availability: "check_doctor_availability",
  book_appointment: "book_appointment",
  cancel_appointment: "cancel_appointment",
};

function argSummary(args: Record<string, unknown>) {
  return Object.entries(args)
    .filter(([k, v]) => v !== undefined && v !== null && v !== "" && k !== "phone_confirmed")
    .map(([k, v]) => {
      let val = String(v);
      if (k === "phone_number") val = val.replace(/\D/g, "").replace(/^(\d{5})(\d)\d\d(\d+)$/, "$1 $2••$3");
      return `${k.replace("_number", "").replace("doctor_id", "doctor").replace("patient_name", "name")}=${val}`;
    })
    .join("  ");
}

export function Activity({ items }: { items: ActivityItem[] }) {
  return (
    <section className="card activity" aria-label="Agent activity">
      <header className="card-head">
        <div>
          <h2>Agent trace</h2>
          <p className="sub">Tool calls as they happen</p>
        </div>
      </header>
      <ol className="trace">
        {items.length === 0 && <li className="empty">Quiet. Start a call to see the agent work.</li>}
        {items.map((it) => (
          <li key={it.id} className={it.kind === "tool" ? `t ${it.tool.state} ${it.tool.ok === false ? "bad" : ""}` : "c"}>
            <time>{it.at.toLocaleTimeString("en-GB", { hour12: false })}</time>
            {it.kind === "tool" ? (
              <>
                <span className="fn">{LABEL[it.tool.name]}</span>
                <span className="args">{argSummary(it.tool.args)}</span>
                <span className="res">
                  {it.tool.state === "running" ? "…" : it.tool.ok ? `ok ${it.tool.ms}ms` : (it.tool.error ?? "error")}
                </span>
              </>
            ) : (
              <span className="evt">{it.text}</span>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
