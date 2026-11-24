import { useEffect, useRef } from "react";

type Props = {
  local: React.RefObject<AnalyserNode | null>;
  remote: React.RefObject<AnalyserNode | null>;
  muted: boolean;
};

const BARS = 34;

/**
 * Live voice waveform. Agent audio draws in pale ivory, the caller in mint;
 * each bar takes the colour of whoever is louder in that band.
 */
export function Waveform({ local, remote, muted }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current!;
    const g = canvas.getContext("2d")!;
    const bufL = new Uint8Array(128);
    const bufR = new Uint8Array(128);
    const smooth = new Float32Array(BARS);
    let raf = 0;
    let t = 0;

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      canvas.width = canvas.clientWidth * dpr;
      canvas.height = canvas.clientHeight * dpr;
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    const band = (buf: Uint8Array, i: number) => {
      // bins 2..50 cover roughly 190 Hz - 4.7 kHz at 48 kHz / fft 256: the voice band
      const lo = 2 + Math.floor((i / BARS) * 48);
      const hi = lo + 2;
      let s = 0;
      for (let k = lo; k < hi; k++) s += buf[k];
      return s / (hi - lo) / 255;
    };

    const draw = () => {
      t += 1;
      const W = canvas.width;
      const H = canvas.height;
      g.clearRect(0, 0, W, H);
      const l = local.current;
      const r = remote.current;
      if (l && !muted) l.getByteFrequencyData(bufL);
      else bufL.fill(0);
      if (r) r.getByteFrequencyData(bufR);
      else bufR.fill(0);

      const gap = W / BARS;
      const bw = Math.max(2, gap * 0.42);
      for (let i = 0; i < BARS; i++) {
        // fold so the loudest bands sit in the middle, like a phone voice memo
        const idx = Math.abs(i - (BARS - 1) / 2) * 2;
        const a = band(bufR, idx);
        const c = band(bufL, idx);
        const v = Math.max(a, c);
        smooth[i] += (v - smooth[i]) * 0.35;
        const idle = 0.04 + 0.025 * Math.sin(t / 22 + i / 2.4);
        const h = Math.max(idle, smooth[i] ** 0.85) * H * 0.92;
        g.fillStyle = c > a ? "rgba(122, 226, 176, 0.95)" : `rgba(244, 238, 226, ${0.35 + Math.min(0.6, smooth[i] * 1.6)})`;
        const x = i * gap + (gap - bw) / 2;
        const y = (H - h) / 2;
        g.beginPath();
        g.roundRect(x, y, bw, h, bw / 2);
        g.fill();
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [local, remote, muted]);

  return <canvas ref={canvasRef} className="wave" aria-hidden />;
}
