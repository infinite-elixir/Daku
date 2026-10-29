import { Link, useNavigate } from "react-router-dom";

import type { SearchResult } from "../lib/api";
import { KindBadge, kindGlowClass } from "./Pills";
import { PosterArt } from "./PosterArt";

export function previewPath(result: Pick<SearchResult, "kind" | "source" | "source_id">): string {
  return `/preview/${encodeURIComponent(result.kind)}/${encodeURIComponent(
    result.source,
  )}/${encodeURIComponent(result.source_id)}`;
}

export function SearchResultCard({
  result,
  libraryId,
  pending,
  onAdd,
}: {
  result: SearchResult;
  libraryId: string | null;
  pending: boolean;
  onAdd: (result: SearchResult) => void;
}) {
  const navigate = useNavigate();
  const inLibrary = libraryId !== null;
  const href = previewPath(result);

  return (
    <article
      onClick={() => navigate(href, { state: { candidate: result } })}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) {
          return;
        }
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          navigate(href, { state: { candidate: result } });
        }
      }}
      tabIndex={0}
      aria-label={`Preview ${result.title}`}
      className={`flex cursor-pointer flex-col rounded-panel border border-border-subtle bg-panel p-panel hover:border-border-strong focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${kindGlowClass(result.kind)}`}
    >
      <Link
        to={href}
        state={{ candidate: result }}
        aria-label={`Preview ${result.title}`}
        onClick={(event) => event.stopPropagation()}
        className="block rounded-panel focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        <PosterArt url={result.poster_url} title={result.title} />
      </Link>
      <div className="mt-2 min-w-0 flex-1">
        <h3 className="line-clamp-2 text-title font-semibold text-fg">
          <Link
            to={href}
            state={{ candidate: result }}
            onClick={(event) => event.stopPropagation()}
            className="hover:underline"
          >
            {result.title}
          </Link>
        </h3>
        <p className="text-small text-fg-muted">
          {result.year ?? "Year unknown"}
          {result.format ? ` · ${result.format}` : ""}
        </p>
      </div>
      <div className="mt-2 flex items-center justify-between gap-2">
        <KindBadge kind={result.kind} />
        {result.anime ? (
          <span
            title={result.classification_reason ?? "Detected via AniBridge mapping"}
            className="inline-flex items-center rounded-full bg-kind-anime/15 px-2 py-0.5 text-micro font-medium text-kind-anime"
          >
            ANIME
          </span>
        ) : null}
        {inLibrary ? (
          <Link
            to={`/title/${libraryId}`}
            onClick={(event) => event.stopPropagation()}
            aria-label={`Open ${result.title}`}
            className="inline-flex min-h-control items-center justify-center rounded-control border border-accent/50 bg-accent/15 px-3 text-small font-semibold text-accent hover:bg-accent/20"
          >
            Open
          </Link>
        ) : (
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              onAdd(result);
            }}
            disabled={pending}
            aria-label={`Add ${result.title} to library`}
            className="inline-flex min-h-control items-center justify-center rounded-control border border-border-strong px-3 text-small font-semibold text-fg transition-colors hover:bg-hover disabled:cursor-not-allowed disabled:border-transparent disabled:bg-transparent disabled:text-fg-muted"
          >
            {pending ? "Adding…" : "Add"}
          </button>
        )}
      </div>
    </article>
  );
}
