import type { SVGProps } from "react";

type P = SVGProps<SVGSVGElement> & { size?: number };

const base = (size = 18): SVGProps<SVGSVGElement> => ({
  width: size,
  height: size,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.7,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
});

/** Linden leaf: the clinic mark. */
export const Leaf = ({ size = 22, ...p }: P) => (
  <svg {...base(size)} {...p} viewBox="0 0 32 32" strokeWidth={1.8}>
    <path d="M16 28c0-6 0-10 0-14" />
    <path d="M16 14c-6.5 0-10-4.2-10-9 4.8 0 10 2.6 10 9Z" fill="currentColor" fillOpacity=".14" />
    <path d="M16 14c6.5 0 10-4.2 10-9-4.8 0-10 2.6-10 9Z" fill="currentColor" fillOpacity=".14" />
    <path d="M16 20c4.6 0 7.4-2.8 7.4-6.4-3.4 0-7.4 1.8-7.4 6.4Z" />
  </svg>
);

export const Phone = ({ size, ...p }: P) => (
  <svg {...base(size)} {...p}>
    <path d="M5.1 3.9c.4-.4 1-.5 1.5-.2l2.6 1.7c.5.3.7 1 .5 1.5l-.9 2.4a1 1 0 0 0 .2 1c1.3 1.6 2.7 3 4.3 4.3.3.2.7.3 1 .2l2.4-.9c.6-.2 1.2 0 1.5.5l1.7 2.6c.3.5.2 1.1-.2 1.5l-1.6 1.6c-.8.8-2 1.1-3.1.7-5-1.8-9-5.8-10.8-10.8-.4-1.1-.1-2.3.7-3.1Z" />
  </svg>
);

export const PhoneDown = ({ size, ...p }: P) => (
  <svg {...base(size)} {...p}>
    <path d="M3.3 13.6c-.5-.5-.6-1.3-.1-1.9C5.5 9.1 8.6 7.8 12 7.8s6.5 1.3 8.8 3.9c.5.6.4 1.4-.1 1.9l-1.6 1.4c-.5.4-1.2.5-1.7.1l-1.9-1.4c-.4-.3-.6-.7-.6-1.2v-1.4a9.6 9.6 0 0 0-5.8 0v1.4c0 .5-.2.9-.6 1.2l-1.9 1.4c-.5.4-1.2.3-1.7-.1Z" fill="currentColor" stroke="none" />
  </svg>
);

export const Mic = ({ size, ...p }: P) => (
  <svg {...base(size)} {...p}>
    <rect x="9" y="3" width="6" height="11" rx="3" />
    <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" />
  </svg>
);

export const MicOff = ({ size, ...p }: P) => (
  <svg {...base(size)} {...p}>
    <path d="M15 10V6a3 3 0 0 0-5.7-1.3M9 9v2a3 3 0 0 0 4.6 2.5" />
    <path d="M5.5 11a6.5 6.5 0 0 0 10.4 5.2M18.5 11c0 .6-.1 1.2-.2 1.8M12 17.5V21M4 4l16 16" />
  </svg>
);

export const Speaker = ({ size, ...p }: P) => (
  <svg {...base(size)} {...p}>
    <path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5Z" />
    <path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11" />
  </svg>
);

export const SpeakerLow = ({ size, ...p }: P) => (
  <svg {...base(size)} {...p}>
    <path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5Z" />
    <path d="M15.5 9a4 4 0 0 1 0 6" />
  </svg>
);

export const Check = ({ size, ...p }: P) => (
  <svg {...base(size)} {...p}>
    <path d="m5 12.5 4.2 4L19 7" />
  </svg>
);

export const Cross = ({ size, ...p }: P) => (
  <svg {...base(size)} {...p}>
    <path d="M6 6l12 12M18 6 6 18" />
  </svg>
);

export const Wave = ({ size, ...p }: P) => (
  <svg {...base(size)} {...p}>
    <path d="M4 12h2M8 8v8M12 5v14M16 9v6M20 12h0" />
  </svg>
);

export const Reset = ({ size, ...p }: P) => (
  <svg {...base(size)} {...p}>
    <path d="M4 12a8 8 0 1 0 2.4-5.7" />
    <path d="M4 4v4.5h4.5" />
  </svg>
);

export const Signal = () => (
  <svg width="17" height="11" viewBox="0 0 17 11" aria-hidden fill="currentColor">
    <rect x="0" y="7" width="3" height="4" rx="1" />
    <rect x="4.5" y="5" width="3" height="6" rx="1" />
    <rect x="9" y="2.5" width="3" height="8.5" rx="1" />
    <rect x="13.5" y="0" width="3" height="11" rx="1" />
  </svg>
);

export const Battery = () => (
  <svg width="25" height="12" viewBox="0 0 25 12" aria-hidden>
    <rect x=".5" y=".5" width="21" height="11" rx="3.5" fill="none" stroke="currentColor" opacity=".45" />
    <rect x="2" y="2" width="15" height="8" rx="2" fill="currentColor" />
    <path d="M23 4v4" stroke="currentColor" strokeLinecap="round" opacity=".45" />
  </svg>
);
