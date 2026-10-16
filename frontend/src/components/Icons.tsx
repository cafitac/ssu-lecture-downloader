import type { SVGProps } from 'react';

function Svg(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    />
  );
}

export const RefreshIcon = () => (
  <Svg><path d="M13.5 8a5.5 5.5 0 1 1-1.7-3.97" /><path d="M13.5 2.5v3h-3" /></Svg>
);
export const SettingsIcon = () => (
  <Svg>
    <path d="M2.5 4.5h4M10.5 4.5h3M2.5 11.5h2M8.5 11.5h5" />
    <circle cx="8.5" cy="4.5" r="1.75" />
    <circle cx="6.5" cy="11.5" r="1.75" />
  </Svg>
);
export const PlayIcon = () => (
  <Svg><path d="M5.5 3.5v9l7-4.5z" fill="currentColor" /></Svg>
);
export const FolderIcon = () => (
  <Svg><path d="M2 4.5a1 1 0 0 1 1-1h3l1.5 1.5H13a1 1 0 0 1 1 1v5.5a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1z" /></Svg>
);
export const DownloadIcon = () => (
  <Svg><path d="M8 2.5v7" /><path d="M5 7l3 3 3-3" /><path d="M3 12.5h10" /></Svg>
);
export const CloseIcon = () => (
  <Svg><path d="M4 4l8 8M12 4l-8 8" /></Svg>
);
export const CheckIcon = () => (
  <Svg><path d="M3.5 8.5l3 3 6-7" /></Svg>
);
export const AlertIcon = () => (
  <Svg><path d="M8 2.5l6 10.5H2z" /><path d="M8 7v2.5" /><path d="M8 11.3v.1" /></Svg>
);
export const ListIcon = () => (
  <Svg><path d="M5.5 4h8M5.5 8h8M5.5 12h8" /><path d="M2.5 4h.1M2.5 8h.1M2.5 12h.1" /></Svg>
);
export const ExternalIcon = () => (
  <Svg><path d="M9 2.5h4.5V7" /><path d="M13.5 2.5L7.5 8.5" /><path d="M11.5 9.5v3a1 1 0 0 1-1 1h-7a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1h3" /></Svg>
);
export const WindowIcon = () => (
  <Svg><rect x="2" y="3" width="12" height="10" rx="1.5" /><path d="M2 6.5h12" /></Svg>
);
export const ChevronIcon = ({ open }: { open: boolean }) => (
  <Svg><path d={open ? 'M4 6l4 4 4-4' : 'M6 4l4 4-4 4'} /></Svg>
);
