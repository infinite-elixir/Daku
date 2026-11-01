import { Link } from "react-router-dom";

import type { TitleSummary, WatchStatus } from "../lib/api";
import { watchStatusLabel } from "../lib/watch";
import { BookmarkIcon, CheckCircleIcon, FolderIcon, HalfCircleIcon } from "./icons";
import { KindBadge, kindGlowClass } from "./Pills";
import { PosterArt } from "./PosterArt";

const WATCH_DOT: Record<WatchStatus, string> = {
  planning: "bg-fg-comment",
  watching: "bg-accent",
  completed: "bg-success",
  dropped: "bg-danger",
  on_hold: "bg-warning",
};

function WatchStatusMark({ status }: { status: WatchStatus }) {
  return (
    <span title={watchStatusLabel(status)} className="inline-flex items-center">
      <span className={`h-2 w-2 rounded-full ${WATCH_DOT[status]}`} aria-hidden />
      <span className="sr-only">{watchStatusLabel(status)}</span>
    </span>
  );
}

function AvailabilityMark({ availability }: { availability: "partial" | "complete" }) {
  if (availability === "complete") {
    return (
      <span title="Available" className="inline-flex items-center text-success">
        <CheckCircleIcon className="h-3.5 w-3.5" />
        <span className="sr-only">Available</span>
      </span>
    );
  }
  return (
    <span title="Partial" className="inline-flex items-center text-warning">
      <HalfCircleIcon className="h-3.5 w-3.5" />
      <span className="sr-only">Partial</span>
    </span>
  );
}

export function LibraryCard({ title }: { title: TitleSummary }) {
  const watched = title.watched_episode_count ?? 0;
  const watchable = title.watchable_episode_count ?? 0;
  const watchPercent = watchable > 0 ? Math.min(100, Math.round((watched / watchable) * 100)) : 0;
  const fileCount = title.file_count ?? 0;

  return (
    <div className="group relative flex min-w-0 flex-col rounded-panel border border-border-subtle bg-panel hover:border-border-strong">
      {/* Stretched link restores whole-card hover and click without nesting the files link. */}
      <Link
        to={`/title/${title.id}`}
        aria-label={title.title}
        className={`absolute inset-0 z-0 rounded-panel focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-accent ${kindGlowClass(title.kind)}`}
      />
      <PosterArt url={title.poster_url} title={title.title} bleed />
      <div className="pointer-events-none mt-panel-compact min-w-0 px-panel">
        <h3 className="truncate text-title font-semibold text-fg">{title.title}</h3>
        <p className="text-small text-fg-muted">{title.year ?? "Year unknown"}</p>
      </div>
      <div className="mt-tight px-panel pb-panel">
        <div className="flex h-5 items-center gap-2.5 text-micro text-fg-muted">
          <KindBadge kind={title.kind} />
          <span className="ml-auto inline-flex items-center gap-2.5">
            {title.watch_status ? <WatchStatusMark status={title.watch_status} /> : null}
            {title.availability !== "none" ? (
              <AvailabilityMark availability={title.availability} />
            ) : null}
            {title.saved_release_count > 0 ? (
              <span
                title={`${title.saved_release_count} saved releases`}
                className="inline-flex items-center gap-1 text-accent"
              >
                <BookmarkIcon className="h-3.5 w-3.5" />
                <span>{title.saved_release_count}</span>
                <span className="sr-only">saved releases</span>
              </span>
            ) : null}
            {fileCount > 0 ? (
              <Link
                to={`/title/${title.id}/files`}
                aria-label={`${fileCount} ${fileCount === 1 ? "file" : "files"}`}
                className="relative z-10 inline-flex items-center gap-1 rounded-control text-fg-muted hover:text-fg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                <FolderIcon className="h-3.5 w-3.5" />
                <span>{fileCount}</span>
              </Link>
            ) : null}
          </span>
        </div>
      </div>
      {title.kind !== "movie" && watchable > 0 ? (
        <div className="absolute inset-x-0 bottom-0 h-0.5 overflow-hidden rounded-b-panel bg-hover">
          <div className="h-full bg-success" style={{ width: `${watchPercent}%` }} />
          <span className="sr-only">{`${watched} / ${watchable} watched`}</span>
        </div>
      ) : null}
    </div>
  );
}
