import { useQuery } from "@tanstack/react-query";
import { useRef } from "react";

import { useLivePlayback } from "../hooks/useLivePlayback";
import {
  getContinueWatching,
  mediaFileThumbnailUrl,
  type ContinueWatchingResponse,
} from "../lib/api";
import { MediaFileCard } from "./MediaFileCard";
import { usePlayerSettings } from "./PlayerProvider";

export function ContinueWatching() {
  const { data } = useQuery<ContinueWatchingResponse>({
    queryKey: ["continue-watching"],
    queryFn: ({ signal }) => getContinueWatching(signal),
    staleTime: 30_000,
  });
  const stripRef = useRef<HTMLDivElement | null>(null);
  const player = usePlayerSettings();
  const active = useLivePlayback(player?.mpv_enabled ?? false);

  const items = data?.items ?? [];
  if (items.length === 0) {
    return null;
  }

  function scrollStrip(direction: -1 | 1) {
    const strip = stripRef.current;
    if (strip === null) {
      return;
    }
    const card = strip.querySelector<HTMLElement>("[data-media-file-card]");
    const amount = (card?.offsetWidth ?? 200) + 16;
    strip.scrollBy({ left: amount * direction, behavior: "smooth" });
  }

  return (
    <section
      data-testid="continue-watching"
      aria-labelledby="continue-heading"
      className="flex flex-col gap-4"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 id="continue-heading" className="text-h2 font-semibold tracking-tight text-fg">
          Continue watching
        </h2>
        <span className="text-small tabular-nums text-fg-muted">
          {items.length} {items.length === 1 ? "title" : "titles"}
        </span>
      </div>

      <div
        ref={stripRef}
        role="region"
        aria-label="Continue watching files"
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === "ArrowRight") {
            event.preventDefault();
            scrollStrip(1);
          } else if (event.key === "ArrowLeft") {
            event.preventDefault();
            scrollStrip(-1);
          }
        }}
        className="continue-strip -mx-1 px-1 pb-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        {items.map((item) => (
          <div key={item.title_id} className="continue-strip-card">
            <MediaFileCard
              file={item.file}
              titleId={item.title_id}
              titleKind={item.kind}
              posterUrl={item.poster_url}
              thumbnailUrl={mediaFileThumbnailUrl(item.file.id)}
              titleName={item.title}
              badge={item.up_next ? "Up next" : null}
              bleed
              livePlayback={active?.media_file_id === item.file.id ? active : null}
            />
          </div>
        ))}
      </div>
    </section>
  );
}
