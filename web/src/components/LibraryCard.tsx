import { Link } from "react-router-dom";

import type { TitleSummary } from "../lib/api";
import { AvailabilityPill, KindBadge, kindGlowClass } from "./Pills";
import { PosterArt } from "./PosterArt";

export function LibraryCard({ title }: { title: TitleSummary }) {
  return (
    <div className="group relative flex min-w-0 flex-col rounded-panel border border-border-subtle bg-panel hover:border-border-strong">
      {/* Stretched link restores whole-card hover and click without nesting the files link. */}
      <Link
        to={`/title/${title.id}`}
        aria-label={title.title}
        className={`absolute inset-0 z-0 rounded-panel focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-accent ${kindGlowClass(title.kind)}`}
      />
      <div className="pointer-events-none flex min-w-0 flex-col p-panel pb-0">
        <PosterArt url={title.poster_url} title={title.title} />
        <div className="mt-panel-compact min-w-0">
          <h3 className="truncate text-title font-semibold text-fg">{title.title}</h3>
          <p className="text-small text-fg-muted">{title.year ?? "Year unknown"}</p>
        </div>
      </div>
      <div className="mt-tight flex flex-wrap items-center gap-2 px-panel pb-panel">
        <KindBadge kind={title.kind} />
        {title.availability !== "none" ? (
          <AvailabilityPill availability={title.availability} />
        ) : null}
        {title.saved_release_count > 0 ? (
          <span className="inline-flex items-center rounded-full bg-accent/15 px-2 py-0.5 text-micro font-medium text-accent">
            {title.saved_release_count} saved
          </span>
        ) : null}
        {(title.file_count ?? 0) > 0 ? (
          <Link
            to={`/title/${title.id}/files`}
            className="relative z-10 rounded-full bg-hover px-2 py-0.5 text-micro font-medium text-fg-muted hover:text-fg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            {title.file_count} {title.file_count === 1 ? "file" : "files"}
          </Link>
        ) : null}
      </div>
    </div>
  );
}
