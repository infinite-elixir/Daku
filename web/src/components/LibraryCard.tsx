import { Link } from "react-router-dom";

import type { TitleSummary } from "../lib/api";
import { AvailabilityPill, KindBadge, kindGlowClass } from "./Pills";
import { PosterArt } from "./PosterArt";

export function LibraryCard({ title }: { title: TitleSummary }) {
  return (
    <Link
      to={`/title/${title.id}`}
      className={`group block rounded-panel border border-border-subtle bg-panel p-2 hover:border-border-strong focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${kindGlowClass(title.kind)}`}
    >
      <PosterArt url={title.poster_url} title={title.title} />
      <div className="mt-2 min-w-0">
        <h3 className="truncate text-title font-semibold text-fg">{title.title}</h3>
        <p className="text-small text-fg-muted">{title.year ?? "Year unknown"}</p>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <KindBadge kind={title.kind} />
        <AvailabilityPill availability={title.availability} />
        {title.saved_release_count > 0 ? (
          <span className="inline-flex items-center rounded-full bg-accent/15 px-2 py-0.5 text-micro font-medium text-accent">
            {title.saved_release_count} saved
          </span>
        ) : null}
      </div>
    </Link>
  );
}
