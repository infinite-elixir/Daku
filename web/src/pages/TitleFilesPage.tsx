import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { MediaFileCard } from "../components/MediaFileCard";
import { usePlayerSettings } from "../components/PlayerProvider";
import { useLivePlayback } from "../hooks/useLivePlayback";
import {
  getTitle,
  getTitleFiles,
  mediaFileThumbnailUrl,
  type MediaFileItem,
  type PlaybackState,
  type TitleDetail,
  type TitleFilesResponse,
} from "../lib/api";
import { formatBytes } from "../lib/format";

const GRID = "grid grid-cols-2 gap-panel sm:grid-cols-4 lg:grid-cols-6";

function newestSeason(seasons: number[]): number | null {
  return seasons.length === 0 ? null : Math.max(...seasons);
}

function LazyFileCard({
  file,
  titleId,
  kind,
  posterUrl,
  livePlayback,
}: {
  file: MediaFileItem;
  titleId: string;
  kind: TitleDetail["kind"];
  posterUrl: string | null;
  livePlayback?: PlaybackState | null;
}) {
  const [visible, setVisible] = useState(() => typeof IntersectionObserver === "undefined");
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (visible) {
      return;
    }
    const node = ref.current;
    if (node === null) {
      return;
    }
    if (typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "200px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [visible]);

  return (
    <div ref={ref} className="min-w-0">
      <MediaFileCard
        file={file}
        titleId={titleId}
        titleKind={kind}
        posterUrl={posterUrl}
        thumbnailUrl={visible ? mediaFileThumbnailUrl(file.id) : null}
        livePlayback={livePlayback ?? null}
      />
    </div>
  );
}

export function TitleFilesPage() {
  const { id = "" } = useParams<{ id: string }>();
  const titleQuery = useQuery<TitleDetail>({
    queryKey: ["title", id],
    queryFn: ({ signal }) => getTitle(id, signal),
    enabled: id.length > 0,
    staleTime: 30_000,
  });
  const filesQuery = useQuery<TitleFilesResponse>({
    queryKey: ["title-files", id],
    queryFn: ({ signal }) => getTitleFiles(id, signal),
    enabled: id.length > 0,
    staleTime: 30_000,
  });
  const player = usePlayerSettings();
  const activePlayback = useLivePlayback(player?.mpv_enabled ?? false);
  const liveFor = (fileId: string) =>
    activePlayback?.media_file_id === fileId ? activePlayback : null;

  const files = filesQuery.data;
  const title = titleQuery.data;
  const seasons = useMemo(
    () => (files?.seasons ?? []).map((season) => season.library_season),
    [files?.seasons],
  );
  const seasonKey = seasons.join(",");
  const [selectedSeason, setSelectedSeason] = useState<number | null>(() => newestSeason(seasons));

  useEffect(() => {
    const available = seasonKey === "" ? [] : seasonKey.split(",").map(Number);
    setSelectedSeason((current) =>
      current !== null && available.includes(current) ? current : newestSeason(available),
    );
  }, [seasonKey]);

  const isMovie = title?.kind === "movie";
  const mainItems = useMemo(() => {
    if (!files) {
      return [];
    }
    if (isMovie) {
      return files.items;
    }
    return files.items.filter(
      (file) => file.has_episode && file.season === selectedSeason,
    );
  }, [files, isMovie, selectedSeason]);
  const extras = useMemo(
    () => (isMovie || !files ? [] : files.items.filter((file) => !file.has_episode)),
    [files, isMovie],
  );

  const error = filesQuery.error ?? titleQuery.error;

  return (
    <section className="flex min-w-0 flex-col gap-section">
      <header className="flex min-w-0 flex-col gap-2">
        <Link
          to={`/title/${id}`}
          className="w-fit text-small font-semibold text-fg-muted hover:text-fg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          ← {title?.title ?? "Title"}
        </Link>
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h1 className="min-w-0 break-words text-h1 font-bold text-fg">
            {title?.title ?? "Files"}
          </h1>
          {files ? (
            <p className="text-small tabular-nums text-fg-muted">
              {files.file_count} {files.file_count === 1 ? "file" : "files"} ·{" "}
              {formatBytes(files.total_size_bytes)}
            </p>
          ) : null}
        </div>
      </header>

      {error ? (
        <div className="w-full rounded-panel border-l-2 border-danger bg-panel px-panel py-panel-compact">
          <p className="text-small text-danger">{error.message}</p>
          <button
            type="button"
            onClick={() => {
              void filesQuery.refetch();
              void titleQuery.refetch();
            }}
            className="mt-2 min-h-control-sm rounded-control border border-border-strong px-3 text-small font-semibold text-fg hover:bg-hover"
          >
            Retry
          </button>
        </div>
      ) : filesQuery.isLoading ? (
        <p className="text-small text-fg-muted">Loading files…</p>
      ) : files && files.file_count === 0 ? (
        <div className="rounded-panel border border-border-subtle bg-panel px-6 py-12 text-center">
          <p className="text-h2 font-semibold tracking-tight text-fg">No files yet</p>
          <Link
            to={`/title/${id}`}
            className="mt-4 inline-flex min-h-control-sm items-center rounded-control border border-border-strong px-3 text-small font-semibold text-fg hover:bg-hover"
          >
            Back to the title
          </Link>
        </div>
      ) : files ? (
        <div className="flex min-w-0 flex-col gap-section">
          {seasons.length > 0 && !isMovie ? (
            <div
              role="tablist"
              aria-label="File seasons"
              className="flex gap-2 overflow-x-auto whitespace-nowrap pb-1 [scrollbar-width:none]"
            >
              {seasons.map((season) => {
                const active = season === selectedSeason;
                return (
                  <button
                    key={season}
                    type="button"
                    role="tab"
                    aria-selected={active}
                    onClick={() => setSelectedSeason(season)}
                    className={[
                      "min-h-row-lg shrink-0 rounded-control border px-4 text-small font-semibold transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
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

          <div className={GRID}>
            {mainItems.map((file) => (
              <LazyFileCard
                key={file.id}
                file={file}
                titleId={files.title_id}
                kind={title?.kind ?? "series"}
                posterUrl={title?.poster_url ?? null}
                livePlayback={liveFor(file.id)}
              />
            ))}
          </div>

          {extras.length > 0 ? (
            <section className="flex min-w-0 flex-col gap-3" aria-labelledby="extras-heading">
              <h2 id="extras-heading" className="text-h2 font-semibold tracking-tight text-fg">
                Extras
              </h2>
              <div className={GRID}>
                {extras.map((file) => (
                  <LazyFileCard
                    key={file.id}
                    file={file}
                    titleId={files.title_id}
                    kind={title?.kind ?? "series"}
                    posterUrl={title?.poster_url ?? null}
                    livePlayback={liveFor(file.id)}
                  />
                ))}
              </div>
            </section>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
