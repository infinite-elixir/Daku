import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useMemo, useState } from "react";

import { DitherCanvas } from "../components/DitherCanvas";
import { KindTabs, type KindFilter } from "../components/KindTabs";
import { LibraryCard } from "../components/LibraryCard";
import { daySeed, selectBackdropIndex } from "../lib/dither";
import { listTitles, type LibraryResponse } from "../lib/api";

const GRID = "grid grid-cols-2 gap-4 sm:grid-cols-4 lg:grid-cols-6";

function mediaMatches(query: string): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia(query).matches
    : false;
}

function canShowBackdrop(): boolean {
  return (
    !mediaMatches("(prefers-reduced-data: reduce)") && !mediaMatches("(max-width: 479px)")
  );
}

function SkeletonGrid() {
  return (
    <div className={GRID} aria-hidden data-testid="library-skeleton">
      {Array.from({ length: 12 }, (_, index) => (
        <div key={index} className="rounded-panel border border-border-subtle bg-panel p-2">
          <div className="aspect-[2/3] animate-pulse rounded-panel bg-hover" />
          <div className="mt-3 h-3 w-4/5 animate-pulse rounded bg-hover" />
          <div className="mt-2 h-3 w-2/5 animate-pulse rounded bg-hover" />
        </div>
      ))}
    </div>
  );
}

function FilterChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={[
        "min-h-9 shrink-0 rounded-control border px-3 text-small font-semibold transition-colors",
        active
          ? "border-accent/50 bg-accent/15 text-accent"
          : "border-border-subtle text-fg-muted hover:bg-hover hover:text-fg",
      ].join(" ")}
    >
      {label}
    </button>
  );
}

export function LibraryPage() {
  const [kind, setKind] = useState<KindFilter>("all");
  const [wantedOnly, setWantedOnly] = useState(false);
  const [noFileOnly, setNoFileOnly] = useState(false);

  const { data, error, isLoading, refetch } = useQuery<LibraryResponse>({
    queryKey: ["library", kind, wantedOnly, noFileOnly],
    queryFn: ({ signal }) =>
      listTitles(
        {
          kind: kind === "all" ? null : kind,
          wanted: wantedOnly ? true : null,
          availability: noFileOnly ? "none" : null,
          sort: "added",
          limit: 100,
        },
        signal,
      ),
    staleTime: 30_000,
  });

  const filtered = kind !== "all" || wantedOnly || noFileOnly;

  const backdrop = useMemo(() => {
    if (!data) {
      return null;
    }
    const posters = data.items
      .filter((item) => item.poster_url)
      .slice()
      .sort((left, right) => (left.poster_url ?? "").localeCompare(right.poster_url ?? ""));
    const index = selectBackdropIndex(posters.length, daySeed());
    return posters[index] ?? null;
  }, [data]);

  return (
    <section className="relative flex flex-col gap-6">
      {backdrop && backdrop.poster_url && canShowBackdrop() ? (
        <div
          data-testid="library-backdrop"
          className="library-backdrop pointer-events-none absolute inset-x-0 top-0 h-80"
          aria-hidden
        >
          <DitherCanvas
            posterUrl={backdrop.poster_url}
            title={backdrop.title}
            year={backdrop.year}
            kind={backdrop.kind}
            width={160}
            height={40}
            className="h-full w-full"
            testId="library-backdrop-art"
          />
        </div>
      ) : null}

      <header className="relative flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-h1 font-bold text-fg">Library</h1>
          <p className="text-small text-fg-muted">
            {data ? `${data.total} ${data.total === 1 ? "title" : "titles"}` : "Your saved titles"}
          </p>
        </div>
        <Link
          to="/search"
          className="inline-flex min-h-9 items-center rounded-control bg-accent px-3 text-small font-semibold text-canvas hover:bg-accent-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Search
        </Link>
      </header>

      <div className="relative flex flex-col gap-2">
        <KindTabs value={kind} onChange={setKind} label="Filter library by kind" />
        <div className="flex gap-2 overflow-x-auto whitespace-nowrap pb-1 [scrollbar-width:none]">
          <FilterChip label="Wanted" active={wantedOnly} onClick={() => setWantedOnly((value) => !value)} />
          <FilterChip label="No file yet" active={noFileOnly} onClick={() => setNoFileOnly((value) => !value)} />
        </div>
      </div>

      {error ? (
        <div className="rounded-panel border-l-2 border-danger bg-panel px-4 py-3">
          <p className="text-small text-danger">{error.message}</p>
          <button
            type="button"
            onClick={() => void refetch()}
            className="mt-2 min-h-9 rounded-control border border-border-strong px-3 text-small font-semibold text-fg hover:bg-hover"
          >
            Retry
          </button>
        </div>
      ) : isLoading ? (
        <SkeletonGrid />
      ) : data && data.items.length === 0 ? (
        <div className="rounded-panel border border-border-subtle bg-panel px-6 py-12 text-center">
          <p className="text-display font-bold leading-none tracking-tight text-border-strong" aria-hidden>
            01
          </p>
          <p className="mt-4 text-h2 font-semibold tracking-tight text-fg">
            {filtered ? "No titles match these filters" : "Library is empty"}
          </p>
          <p className="mx-auto mt-1 max-w-[48ch] text-small text-fg-muted">
            {filtered
              ? "Clear a filter or search for something new."
              : "Search for a title to add it. Saved entries stay here even with no file."}
          </p>
          <Link
            to="/search"
            className="mt-4 inline-flex min-h-9 items-center rounded-control border border-border-strong px-3 text-small font-semibold text-fg hover:bg-hover"
          >
            Search the catalogue
          </Link>
        </div>
      ) : data ? (
        <div className={GRID}>
          {data.items.map((title) => (
            <LibraryCard key={title.id} title={title} />
          ))}
        </div>
      ) : null}
    </section>
  );
}
