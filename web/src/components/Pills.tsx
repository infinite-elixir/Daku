import type { Availability, Kind } from "../lib/api";

const KIND_STYLE: Record<Kind, { label: string; badge: string; glow: string }> = {
  movie: {
    label: "MOVIE",
    badge: "text-kind-movie",
    glow: "poster-glow kind-glow-movie",
  },
  series: {
    label: "TV",
    badge: "text-kind-series",
    glow: "poster-glow kind-glow-series",
  },
  anime: {
    label: "ANIME",
    badge: "text-kind-anime",
    glow: "poster-glow kind-glow-anime",
  },
};

const AVAILABILITY_STYLE: Record<Availability, { label: string; className: string }> = {
  none: { label: "Saved", className: "bg-hover text-fg-muted" },
  partial: { label: "Partial", className: "bg-warning/15 text-warning" },
  complete: { label: "Available", className: "bg-success/15 text-success" },
};

const EPISODE_STATUS: Record<string, { label: string; className: string }> = {
  available: { label: "Available", className: "bg-success/15 text-success" },
  downloading: { label: "Downloading", className: "bg-accent/15 text-accent" },
  active: { label: "Active", className: "bg-accent/15 text-accent" },
  queued: { label: "Queued", className: "bg-hover text-fg-muted" },
  paused: { label: "Paused", className: "bg-warning/15 text-warning" },
  failed: { label: "Failed", className: "bg-danger/15 text-danger" },
  cancelled: { label: "Cancelled", className: "bg-danger/15 text-danger" },
  missing: { label: "No file", className: "bg-hover text-fg-comment" },
};

const PILL = "inline-flex items-center px-2 py-0.5 text-micro font-medium";

export function KindBadge({ kind }: { kind: Kind }) {
  const style = KIND_STYLE[kind];
  return (
    <span className={`text-micro font-semibold uppercase tracking-wide ${style.badge}`}>
      {style.label}
    </span>
  );
}

export function kindGlowClass(kind: Kind): string {
  return KIND_STYLE[kind].glow;
}

export function AvailabilityPill({ availability }: { availability: Availability }) {
  const style = AVAILABILITY_STYLE[availability];
  return <span className={`${PILL} rounded-full ${style.className}`}>{style.label}</span>;
}

export function EpisodeStatusPill({ status }: { status: string }) {
  const style = EPISODE_STATUS[status] ?? {
    label: status,
    className: "bg-hover text-fg-muted",
  };
  return <span className={`${PILL} rounded-full ${style.className}`}>{style.label}</span>;
}
