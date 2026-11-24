// Call sounds, synthesised with Web Audio so there are no audio assets to ship.

let ctx: AudioContext | null = null;
const audio = () => (ctx ??= new AudioContext());

function tone(freqs: number[], start: number, duration: number, gain = 0.06, out?: AudioNode) {
  const c = audio();
  const g = c.createGain();
  g.gain.setValueAtTime(0, start);
  g.gain.linearRampToValueAtTime(gain, start + 0.02);
  g.gain.setValueAtTime(gain, start + duration - 0.03);
  g.gain.linearRampToValueAtTime(0, start + duration);
  g.connect(out ?? c.destination);
  for (const f of freqs) {
    const o = c.createOscillator();
    o.type = "sine";
    o.frequency.value = f;
    o.connect(g);
    o.start(start);
    o.stop(start + duration + 0.01);
  }
}

/**
 * Indian ringback: 400 + 450 Hz, "ring-ring" (0.4s on, 0.2s off, 0.4s on) then 2s silence.
 * Returns a stop function.
 */
export function startRingback(): () => void {
  const c = audio();
  void c.resume();
  const bus = c.createGain();
  bus.connect(c.destination);
  let next = c.currentTime + 0.05;
  const schedule = () => {
    while (next < c.currentTime + 4) {
      tone([400, 450], next, 0.4, 0.05, bus);
      tone([400, 450], next + 0.6, 0.4, 0.05, bus);
      next += 3;
    }
  };
  schedule();
  const timer = window.setInterval(schedule, 1000);
  return () => {
    window.clearInterval(timer);
    bus.gain.setTargetAtTime(0, c.currentTime, 0.02);
    window.setTimeout(() => bus.disconnect(), 200);
  };
}

/** Three short descending beeps, like a line going dead. */
export function playHangup() {
  const c = audio();
  void c.resume();
  const t = c.currentTime + 0.02;
  [620, 520, 420].forEach((f, i) => tone([f], t + i * 0.18, 0.12, 0.05));
}

/** Soft two-note chime for a confirmed booking. */
export function playChime(kind: "booked" | "cancelled") {
  const c = audio();
  void c.resume();
  const t = c.currentTime + 0.02;
  const notes = kind === "booked" ? [659.25, 987.77] : [587.33, 440];
  notes.forEach((f, i) => tone([f], t + i * 0.12, 0.32, 0.035));
}
