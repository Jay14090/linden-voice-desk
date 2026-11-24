import { PipecatClient, type BotOutputData, type Participant, type UserInputData } from "@pipecat-ai/client-js";
import { SmallWebRTCTransport } from "@pipecat-ai/small-webrtc-transport";
import { useCallback, useEffect, useRef, useState } from "react";

import type { Appointment } from "./api";
import { playChime, playHangup, startRingback } from "./sounds";

export type CallState = "idle" | "dialing" | "connected" | "ended" | "failed";

export type Line =
  | { id: string; kind: "caller"; text: string; final: boolean }
  | { id: string; kind: "agent"; text: string; done: boolean }
  | { id: string; kind: "card"; action: "booked" | "cancelled" | "rescheduled"; appointment: Appointment };

const MIN_RING_MS = 1400; // at least one full ring, like a real line

let lineSeq = 0;
const nextId = () => `l${++lineSeq}`;

export function useCall() {
  const [state, setState] = useState<CallState>("idle");
  const [lines, setLines] = useState<Line[]>([]);
  const [muted, setMuted] = useState(false);
  const [speaker, setSpeaker] = useState(true);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [endedAfter, setEndedAfter] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [speaking, setSpeaking] = useState<"agent" | "caller" | null>(null);

  const clientRef = useRef<PipecatClient | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const stopRingRef = useRef<(() => void) | null>(null);
  const stateRef = useRef<CallState>("idle");
  const startedRef = useRef<number | null>(null);
  const agentLineRef = useRef<string | null>(null);
  const callerLineRef = useRef<string | null>(null);

  // Analysers feed the waveform. Kept in refs so the canvas can read them every frame.
  const audioCtxRef = useRef<AudioContext | null>(null);
  const localAnalyser = useRef<AnalyserNode | null>(null);
  const remoteAnalyser = useRef<AnalyserNode | null>(null);

  const setCallState = (s: CallState) => {
    stateRef.current = s;
    setState(s);
  };

  const attachAnalyser = (track: MediaStreamTrack, which: "local" | "remote") => {
    const ctx = (audioCtxRef.current ??= new AudioContext());
    void ctx.resume();
    const src = ctx.createMediaStreamSource(new MediaStream([track]));
    const an = ctx.createAnalyser();
    an.fftSize = 256;
    an.smoothingTimeConstant = 0.72;
    src.connect(an);
    (which === "local" ? localAnalyser : remoteAnalyser).current = an;
  };

  // ---------- transcript ----------

  const onUserInput = (d: UserInputData) => {
    if (!d.text?.trim()) return;
    agentLineRef.current = null; // the caller spoke; the next agent words start a new bubble
    let id = callerLineRef.current;
    if (!id) {
      id = nextId();
      callerLineRef.current = id;
    }
    const line: Line = { id, kind: "caller", text: d.text, final: d.final };
    setLines((prev) => (prev.some((l) => l.id === id) ? prev.map((l) => (l.id === id ? line : l)) : [...prev, line]));
    if (d.final) callerLineRef.current = null;
  };

  // The bot reports what it says as sentences. We append each sentence to the
  // current agent bubble; a new bubble starts after the caller speaks.
  const onBotOutput = (d: BotOutputData) => {
    const type = d.output_type ?? d.aggregated_by ?? "sentence";
    if (type !== "sentence" || !d.text?.trim()) return;
    if (d.spoken_status && d.spoken_status !== "new") return;
    const text = d.text.trim();
    let id = agentLineRef.current;
    const isNew = !id;
    if (!id) {
      id = nextId();
      agentLineRef.current = id;
    }
    const lineId = id;
    setLines((prev) =>
      isNew || !prev.some((l) => l.id === lineId)
        ? [...prev, { id: lineId, kind: "agent", text, done: false }]
        : prev.map((l) => (l.id === lineId && l.kind === "agent" ? { ...l, text: `${l.text} ${text}`, done: false } : l)),
    );
  };

  // ---------- lifecycle ----------

  const cleanup = useCallback(() => {
    stopRingRef.current?.();
    stopRingRef.current = null;
    localAnalyser.current = null;
    remoteAnalyser.current = null;
    if (audioRef.current) audioRef.current.srcObject = null;
  }, []);

  const finish = useCallback(
    (failed?: string) => {
      if (stateRef.current === "ended" || stateRef.current === "failed" || stateRef.current === "idle") return;
      const wasConnected = stateRef.current === "connected";
      cleanup();
      setSpeaking(null);
      if (failed && !wasConnected) {
        setError(failed);
        setCallState("failed");
      } else {
        setEndedAfter(startedRef.current ? Math.round((Date.now() - startedRef.current) / 1000) : 0);
        setCallState("ended");
      }
      playHangup();
      const c = clientRef.current;
      clientRef.current = null;
      void c?.disconnect().catch(() => {});
    },
    [cleanup],
  );

  const start = useCallback(async () => {
    if (stateRef.current === "dialing" || stateRef.current === "connected") return;
    setLines([]);
    setError(null);
    setMuted(false);
    setStartedAt(null);
    startedRef.current = null;
    agentLineRef.current = null;
    callerLineRef.current = null;
    setCallState("dialing");
    stopRingRef.current = startRingback();
    const dialedAt = Date.now();

    const client = new PipecatClient({
      transport: new SmallWebRTCTransport(),
      enableMic: true,
      enableCam: false,
      callbacks: {
        onBotReady: () => {
          const wait = Math.max(0, MIN_RING_MS - (Date.now() - dialedAt));
          window.setTimeout(() => {
            if (stateRef.current !== "dialing") return;
            stopRingRef.current?.();
            stopRingRef.current = null;
            const now = Date.now();
            startedRef.current = now;
            setStartedAt(now);
            setCallState("connected");
            if (audioRef.current) audioRef.current.muted = false;
          }, wait);
        },
        onTrackStarted: (track: MediaStreamTrack, participant?: Participant) => {
          if (track.kind !== "audio") return;
          if (participant?.local) {
            attachAnalyser(track, "local");
          } else {
            if (audioRef.current) {
              audioRef.current.srcObject = new MediaStream([track]);
              // stay silent until the "pickup" so the greeting doesn't play over the ring
              audioRef.current.muted = stateRef.current !== "connected";
              void audioRef.current.play().catch(() => {});
            }
            attachAnalyser(track, "remote");
          }
        },
        onUserInput,
        onBotOutput,
        onUserStartedSpeaking: () => setSpeaking("caller"),
        onUserStoppedSpeaking: () => setSpeaking((s) => (s === "caller" ? null : s)),
        onBotStartedSpeaking: () => setSpeaking("agent"),
        onBotStoppedSpeaking: () => {
          setSpeaking((s) => (s === "agent" ? null : s));
          const id = agentLineRef.current;
          if (id) setLines((prev) => prev.map((l) => (l.id === id && l.kind === "agent" ? { ...l, done: true } : l)));
        },
        onServerMessage: (data: { type?: string; appointment?: Appointment; text?: string }) => {
          // pre-rendered lines (the greeting) arrive as a server message, not bot-output
          if (data?.type === "agent.line" && data.text) {
            const line: Line = { id: nextId(), kind: "agent", text: data.text, done: true };
            agentLineRef.current = null;
            setLines((prev) => [...prev, line]);
            return;
          }
          if (data?.type === "appointment.booked" || data?.type === "appointment.cancelled" || data?.type === "appointment.rescheduled") {
            const action = data.type.replace("appointment.", "") as "booked" | "cancelled" | "rescheduled";
            agentLineRef.current = null;
            const card: Line = { id: nextId(), kind: "card", action, appointment: data.appointment! };
            setLines((prev) => [...prev, card]);
            playChime(action === "cancelled" ? "cancelled" : "booked");
          }
        },
        onDisconnected: () => finish(),
        onBotDisconnected: () => finish(),
        onError: (msg) => {
          console.error("pipecat error", msg);
        },
        onDeviceError: () => finish("Microphone access is blocked. Allow it in the address bar and try again."),
      },
    });
    clientRef.current = client;

    try {
      await client.initDevices();
      await client.connect({ webrtcRequestParams: { endpoint: "/api/offer" } });
    } catch (e) {
      console.error(e);
      const msg = e instanceof Error ? e.message : String(e);
      finish(/permission|notallowed|denied/i.test(msg) ? "Microphone access is blocked. Allow it and try again." : "Couldn't reach the front desk. Is the server running?");
    }
  }, [finish]);

  const hangUp = useCallback(() => finish(), [finish]);

  const reset = useCallback(() => {
    if (stateRef.current === "dialing" || stateRef.current === "connected") return;
    setCallState("idle");
  }, []);

  const toggleMute = useCallback(() => {
    const c = clientRef.current;
    if (!c) return;
    const next = !muted;
    c.enableMic(!next);
    setMuted(next);
  }, [muted]);

  const toggleSpeaker = useCallback(() => {
    setSpeaker((s) => {
      if (audioRef.current) audioRef.current.volume = s ? 0.15 : 1;
      return !s;
    });
  }, []);

  useEffect(() => {
    const el = document.createElement("audio");
    el.autoplay = true;
    audioRef.current = el;
    return () => {
      void clientRef.current?.disconnect();
      cleanup();
    };
  }, [cleanup]);

  return {
    state,
    lines,
    muted,
    speaker,
    startedAt,
    endedAfter,
    error,
    speaking,
    analysers: { local: localAnalyser, remote: remoteAnalyser },
    start,
    hangUp,
    reset,
    toggleMute,
    toggleSpeaker,
  };
}

export type CallApi = ReturnType<typeof useCall>;
