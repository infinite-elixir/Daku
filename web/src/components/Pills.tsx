import type { Availability, Kind } from "../lib/api";

const KIND_STYLE: Record<Kind, { label: string; badge: string; edge: string }> = {
  movie: {
    label: "MOVIE",
    badge: "bg-kind-movie/15 text-kind-movie",
    edge: "bg-kind-movie",
  },
  series: {
    label: "TV",
    badge: "bg-kind-series/15 text-kind-series",
    edge: "bg-kind-series",
  },
  anime: {
    label: "ANIME",
    badge: "bg-kind-anime/15 text-kind-anime",
    edge: "bg-kind-anime",
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

const PILL = "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium leading-4";

export function KindBadge({ kind }: { kind: Kind }) {
  const style = KIND_STYLE[kind];
  return <span className={`${PILL} ${style.badge}`}>{style.label}</span>;
}

export function kindEdgeClass(kind: Kind): string {
  return KIND_STYLE[kind].edge;
}

export function AvailabilityPill({ availability }: { availability: Availability }) {
  const style = AVAILABILITY_STYLE[availability];
  return <span className={`${PILL} ${style.className}`}>{style.label}</span>;
}

export function EpisodeStatusPill({ status }: { status: string }) {
  const style = EPISODE_STATUS[status] ?? {
    label: status,
    className: "bg-hover text-fg-muted",
  };
  return <span className={`${PILL} ${style.className}`}>{style.label}</span>;
}
