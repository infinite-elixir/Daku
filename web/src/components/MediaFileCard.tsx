import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import type { Kind, MediaFileItem, PlaybackState } from "../lib/api";
import { playMediaFile } from "../lib/api";
import { formatBytes } from "../lib/format";
import { usePlayerSettings } from "./PlayerProvider";

const PLACEHOLDER: Record<Kind, string> = {
  movie: "bg-kind-movie/15",
  series: "bg-kind-series/15",
  anime: "bg-kind-anime/15",
};

const PLACEHOLDER_TEXT: Record<Kind, string> = {
  movie: "text-kind-movie/70",
  series: "text-kind-series/70",
  anime: "text-kind-anime/70",
};

const CARD_CLASS =
  "group flex min-h-row-lg min-w-0 flex-col gap-2 rounded-panel border border-border-subtle bg-panel p-panel-compact text-left transition-colors hover:border-border-strong focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";
/** Art fills the card's outer frame, like a library poster card; the text keeps its padding. */
const BLEED_CARD_CLASS =
  "group flex min-h-row-lg min-w-0 flex-col overflow-hidden rounded-panel border border-border-subtle bg-panel text-left transition-colors hover:border-border-strong focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";
const HERO_CLASS =
  "group relative block w-full max-w-[560px] overflow-hidden rounded-panel border border-border-subtle bg-panel text-left transition-colors hover:border-border-strong focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

const TOAST_TTL_MS = 8000;
const DRAG_THRESHOLD_PX = 8;

function formatClock(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  const mm = String(minutes).padStart(hours > 0 ? 2 : 1, "0");
  const ss = String(rest).padStart(2, "0");
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`;
}

export interface MediaFileCardProps {
  file: MediaFileItem;
  titleId: string;
  titleKind: Kind;
  posterUrl?: string | null;
  /** Omit until the card is visible on the files page. A 404 falls back to the poster. */
  thumbnailUrl?: string | null;
  /** `hero` is the single wide movie treatment; `card` is the strip/grid card. */
  variant?: "card" | "hero";
  /** Test/override hook; by default the value comes from read-only player settings. */
  mpvEnabled?: boolean;
  /** Live position from the host player; falls back to the persisted value on the file. */
  livePlayback?: PlaybackState | null;
  /** Library-wide rows need to name the title the file belongs to. */
  titleName?: string | null;
  /** Small pill over the artwork, e.g. "Up next". */
  badge?: string | null;
  /** Let the artwork touch the card's outer border instead of sitting in an inner box. */
  bleed?: boolean;
}

export function MediaFileCard({
  file,
  titleId,
  titleKind,
  posterUrl = null,
  thumbnailUrl = null,
  variant = "card",
  mpvEnabled,
  livePlayback = null,
  titleName = null,
  badge = null,
  bleed = false,
}: MediaFileCardProps) {
  const player = usePlayerSettings();
  const launchEnabled = mpvEnabled ?? player?.mpv_enabled ?? false;
  const [thumbnailFailed, setThumbnailFailed] = useState(false);
  const [posterFailed, setPosterFailed] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pointerStart = useRef<{ x: number; y: number; target: EventTarget | null } | null>(null);

  useEffect(() => {
    setThumbnailFailed(false);
  }, [thumbnailUrl]);

  useEffect(() => {
    setPosterFailed(false);
  }, [posterUrl]);

  useEffect(() => {
    if (error === null) {
      return;
    }
    const timer = window.setTimeout(() => setError(null), TOAST_TTL_MS);
    return () => window.clearTimeout(timer);
  }, [error]);

  async function launch() {
    if (pending) {
      return;
    }
    setPending(true);
    setError(null);
    try {
      await playMediaFile(file.id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not launch mpv");
    } finally {
      setPending(false);
    }
  }

  function onPointerDown(event: React.PointerEvent) {
    pointerStart.current = { x: event.clientX, y: event.clientY, target: event.target };
  }

  function onClick(event: React.MouseEvent) {
    const start = pointerStart.current;
    pointerStart.current = null;
    if (start !== null) {
      const moved = Math.hypot(event.clientX - start.x, event.clientY - start.y);
      // A carousel drag or a touch scroll must never launch a player.
      if (moved > DRAG_THRESHOLD_PX || start.target !== event.target) {
        return;
      }
    }
    void launch();
  }

  const primary = file.has_episode
    ? file.episode_name ?? file.file_name
    : file.file_name;
  const showThumbnail = Boolean(thumbnailUrl) && !thumbnailFailed;
  const showPoster = !showThumbnail && Boolean(posterUrl) && !posterFailed;

  const art = showThumbnail ? (
    <img
      src={thumbnailUrl ?? ""}
      alt=""
      loading="lazy"
      decoding="async"
      onError={() => setThumbnailFailed(true)}
      className="h-full w-full object-cover"
    />
  ) : showPoster ? (
    <img
      src={posterUrl ?? ""}
      alt=""
      loading="lazy"
      decoding="async"
      onError={() => setPosterFailed(true)}
      className="h-full w-full object-cover"
    />
  ) : (
    <div
      aria-hidden
      className={`flex h-full w-full items-center justify-center ${PLACEHOLDER[titleKind]}`}
    >
      <span className={`text-h2 font-semibold ${PLACEHOLDER_TEXT[titleKind]}`}>
        {titleKind === "anime" ? "ANI" : titleKind === "series" ? "TV" : "MOV"}
      </span>
    </div>
  );

  const episodeLabel = file.has_episode && file.episode_label ? (
    <span className="shrink-0 font-mono text-micro tabular-nums">{file.episode_label}</span>
  ) : null;

  const busy = pending ? (
    <span className="absolute right-2 top-2 z-10 rounded-full bg-canvas/85 px-2 py-0.5 text-micro font-semibold text-fg">
      Opening…
    </span>
  ) : null;

  const played = livePlayback ?? file.playback ?? null;
  const totalSeconds =
    played !== null && played.duration_s !== null && played.duration_s > 0
      ? played.duration_s
      : null;
  const progressRatio =
    played !== null && totalSeconds !== null
      ? Math.min(1, Math.max(0, played.position_s / totalSeconds))
      : 0;

  // Daku launches mpv; the play affordance only appears while the player is enabled.
  const playOverlay = launchEnabled ? (
    <span
      aria-hidden
      data-testid={`media-file-play-overlay-${file.id}`}
      className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-visible:opacity-100"
    >
      <span className="flex h-12 w-12 items-center justify-center rounded-full bg-canvas/80 text-fg shadow-lg backdrop-blur-sm">
        <svg viewBox="0 0 24 24" className="ml-0.5 h-6 w-6 fill-current" aria-hidden>
          <path d="M8 5v14l11-7z" />
        </svg>
      </span>
    </span>
  ) : null;

  const seekBar =
    played !== null && totalSeconds !== null && progressRatio > 0 ? (
      <div
        aria-hidden
        data-testid={`media-file-progress-${file.id}`}
        title={`${formatClock(played.position_s)} of ${formatClock(totalSeconds)}`}
        className="pointer-events-none absolute inset-x-0 bottom-0 z-10 h-1 bg-black/45"
      >
        <div
          className="h-full bg-accent"
          style={{ width: `${(progressRatio * 100).toFixed(1)}%` }}
        />
      </div>
    ) : null;

  const body =
    variant === "hero" ? (
      <div className="relative aspect-video w-full overflow-hidden bg-panel-alt">
        {art}
        <div className="media-hero-scrim absolute inset-x-0 bottom-0 px-panel pb-panel pt-12">
          <p className="flex min-w-0 items-baseline gap-2 text-title font-semibold text-white">
            {episodeLabel}
            <span className="truncate">{primary}</span>
          </p>
          <p className="mt-0.5 text-small text-white/80">{formatBytes(file.size_bytes)}</p>
        </div>
        {playOverlay}
        {seekBar}
        {busy}
      </div>
    ) : (
      <>
        <div
          className={[
            "relative aspect-video w-full overflow-hidden bg-panel-alt",
            bleed ? "" : "rounded-panel",
          ].join(" ")}
        >
          {art}
          {playOverlay}
          {seekBar}
          {busy}
          {badge !== null ? (
            <span className="pointer-events-none absolute left-2 top-2 z-10 rounded-full bg-accent px-2 py-0.5 text-micro font-semibold text-canvas">
              {badge}
            </span>
          ) : null}
        </div>
        <div className={bleed ? "min-w-0 px-panel-compact pb-panel-compact pt-2" : "min-w-0"}>
          {titleName !== null ? (
            <p className="truncate text-micro font-medium text-fg-muted">{titleName}</p>
          ) : null}
          <p className="flex min-w-0 items-baseline gap-1.5 text-small">
            {file.has_episode && file.episode_label ? (
              <span className="shrink-0 font-mono text-micro tabular-nums text-fg-muted">
                {file.episode_label}
              </span>
            ) : null}
            <span className="truncate font-medium text-fg">{primary}</span>
          </p>
          <p className="mt-0.5 text-micro text-fg-muted">{formatBytes(file.size_bytes)}</p>
        </div>
      </>
    );

  const card =
    launchEnabled ? (
      <button
        type="button"
        id={`media-file-${file.id}`}
        data-testid={`media-file-card-${file.id}`}
        data-media-file-card
        aria-busy={pending}
        onPointerDown={onPointerDown}
        onClick={onClick}
        className={
          variant === "hero" ? HERO_CLASS : `${bleed ? BLEED_CARD_CLASS : CARD_CLASS} w-full`
        }
      >
        {body}
      </button>
    ) : (
      <Link
        id={`media-file-${file.id}`}
        to={`/title/${titleId}/files#media-file-${file.id}`}
        data-testid={`media-file-card-${file.id}`}
        data-media-file-card
        className={variant === "hero" ? HERO_CLASS : bleed ? BLEED_CARD_CLASS : CARD_CLASS}
      >
        {body}
      </Link>
    );

  return (
    <>
      {card}
      {error !== null ? (
        <div
          role="alert"
          className="fixed bottom-4 right-4 z-50 max-w-sm rounded-panel border border-danger/40 bg-panel px-panel py-panel-compact text-small text-danger shadow-lg"
        >
          {error}
        </div>
      ) : null}
    </>
  );
}
