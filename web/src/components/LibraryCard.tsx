import { Link } from "react-router-dom";

import type { TitleSummary } from "../lib/api";
import { AvailabilityPill, KindBadge } from "./Pills";
import { PosterArt } from "./PosterArt";

export function LibraryCard({ title }: { title: TitleSummary }) {
  return (
    <Link
      to={`/title/${title.id}`}
      className="group block rounded-panel border border-border-subtle bg-panel p-2 transition-colors hover:border-border-strong focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      <PosterArt url={title.poster_url} title={title.title} kind={title.kind} />
      <div className="mt-2 min-w-0">
        <h3 className="truncate text-[14px] font-semibold leading-5 text-fg">{title.title}</h3>
        <p className="text-[12px] leading-4 text-fg-muted">{title.year ?? "Year unknown"}</p>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <KindBadge kind={title.kind} />
        <AvailabilityPill availability={title.availability} />
        {title.saved_release_count > 0 ? (
          <span className="inline-flex items-center rounded-full bg-accent/15 px-2 py-0.5 text-[11px] font-medium leading-4 text-accent">
            {title.saved_release_count} saved
          </span>
        ) : null}
      </div>
    </Link>
  );
}
