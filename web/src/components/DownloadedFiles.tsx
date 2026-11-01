import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import type { Kind, TitleFilesResponse } from "../lib/api";
import { mediaFileThumbnailUrl } from "../lib/api";
import { formatBytes } from "../lib/format";
import { useLivePlayback } from "../hooks/useLivePlayback";
import { MediaFileCard } from "./MediaFileCard";
import { usePlayerSettings } from "./PlayerProvider";

export interface DownloadedFilesProps {
  files: TitleFilesResponse;
  titleKind: Kind;
  posterUrl?: string | null;
  /** The open episode tab drives the default season; it never restricts the header total. */
  activeSeason?: number | null;
}

function newestSeason(seasons: number[]): number | null {
  return seasons.length === 0 ? null : Math.max(...seasons);
}

export function DownloadedFiles({
  files,
  titleKind,
  posterUrl = null,
  activeSeason = null,
}: DownloadedFilesProps) {
  const seasons = useMemo(
    () => files.seasons.map((season) => season.library_season),
    [files.seasons],
  );
  const seasonKey = seasons.join(",");
  const isMovie = titleKind === "movie";
  const [selectedSeason, setSelectedSeason] = useState<number | null>(() =>
    !isMovie && activeSeason !== null && seasons.includes(activeSeason)
      ? activeSeason
      : newestSeason(seasons),
  );

  useEffect(() => {
    const available = seasonKey === "" ? [] : seasonKey.split(",").map(Number);
    setSelectedSeason((current) => {
      if (!isMovie && activeSeason !== null && available.includes(activeSeason)) {
        return activeSeason;
      }
      if (current !== null && available.includes(current)) {
        return current;
      }
      return newestSeason(available);
    });
  }, [activeSeason, isMovie, seasonKey]);

  const visible = useMemo(() => {
    if (isMovie) {
      return files.items;
    }
    return files.items.filter(
      (file) => file.has_episode && file.season === selectedSeason,
    );
  }, [files.items, isMovie, selectedSeason]);

  // A movie gets one wide card: its primary file, never a strip of samples.
  const movieFile = useMemo(() => {
    if (!isMovie) {
      return null;
    }
    return files.items.find((file) => file.is_primary) ?? files.items[0] ?? null;
  }, [files.items, isMovie]);

  const stripRef = useRef<HTMLDivElement | null>(null);
  const player = usePlayerSettings();
  const active = useLivePlayback(player?.mpv_enabled ?? false);
  const liveFor = (id: string) => (active?.media_file_id === id ? active : null);

  if (files.file_count === 0) {
    return null;
  }

  function scrollStrip(direction: -1 | 1) {
    const strip = stripRef.current;
    if (strip === null) {
      return;
    }
    const card = strip.querySelector<HTMLElement>("[data-media-file-card]");
    const amount = (card?.offsetWidth ?? 240) + 16;
    strip.scrollBy({ left: amount * direction, behavior: "smooth" });
  }

  return (
    <section
      data-testid="downloaded-files"
      aria-labelledby="downloaded-heading"
      className="flex flex-col gap-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
        <div className="flex min-w-0 flex-col gap-1">
          <h2 id="downloaded-heading" className="text-h2 font-semibold tracking-tight text-fg">
            Downloaded
          </h2>
          <span className="text-small tabular-nums text-fg-muted">
            {files.file_count} {files.file_count === 1 ? "file" : "files"} ·{" "}
            {formatBytes(files.total_size_bytes)}
          </span>
        </div>
        {files.file_count > 1 ? (
          <Link
            to={`/title/${files.title_id}/files`}
            className="text-small font-semibold text-fg-muted underline-offset-2 hover:text-fg hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            View all files ({files.file_count})
          </Link>
        ) : null}
      </div>

      {seasons.length > 1 ? (
        <div
          role="group"
          aria-label="Filter downloaded files by season"
          className="flex gap-2 overflow-x-auto whitespace-nowrap pb-1 [scrollbar-width:none]"
        >
          {seasons.map((season) => {
            const active = season === selectedSeason;
            return (
              <button
                key={season}
                type="button"
                aria-pressed={active}
                onClick={() => setSelectedSeason(season)}
                className={[
                  "min-h-control-sm shrink-0 rounded-control border px-3 text-small font-semibold transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
                  active
                    ? "border-accent/60 bg-accent/15 text-accent"
                    : "border-border-subtle text-fg-muted hover:bg-hover hover:text-fg",
                ].join(" ")}
              >
                {season === 0 ? "Specials" : `Season ${season}`}
              </button>
            );
          })}
        </div>
      ) : null}

      {isMovie ? (
        movieFile ? (
          <MediaFileCard
            file={movieFile}
            titleId={files.title_id}
            titleKind={titleKind}
            posterUrl={posterUrl}
            thumbnailUrl={mediaFileThumbnailUrl(movieFile.id)}
            variant="hero"
            livePlayback={liveFor(movieFile.id)}
          />
        ) : null
      ) : (
        <div
          ref={stripRef}
          role="region"
          aria-label="Downloaded files"
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
          className="downloaded-strip -mx-1 px-1 pb-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {visible.map((file) => (
            <div key={file.id} className="downloaded-strip-card">
              <MediaFileCard
                file={file}
                titleId={files.title_id}
                titleKind={titleKind}
                posterUrl={posterUrl}
                thumbnailUrl={mediaFileThumbnailUrl(file.id)}
                livePlayback={liveFor(file.id)}
              />
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
