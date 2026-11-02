import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useState } from "react";

import { ContinueWatching } from "../components/ContinueWatching";
import { LibraryCard } from "../components/LibraryCard";
import { ChevronDownIcon } from "../components/icons";
import { listTitles, type LibraryResponse, type WatchStatusFilter } from "../lib/api";
import { LIBRARY_STATUS_FILTERS, WATCH_STATUS_LABELS } from "../lib/watch";

const GRID = "grid grid-cols-2 gap-card sm:grid-cols-4 lg:grid-cols-6";
const PRIMARY_STATUSES: WatchStatusFilter[] = ["watching"];
const KIND_OPTIONS = [
  { value: "all", label: "All" },
  { value: "movie", label: "Movies" },
  { value: "series", label: "TV" },
  { value: "anime", label: "Anime" },
] as const;
type KindFilter = (typeof KIND_OPTIONS)[number]["value"];

function SkeletonGrid() {
  return (
    <div className={GRID} aria-hidden data-testid="library-skeleton">
      {Array.from({ length: 12 }, (_, index) => (
        <div key={index} className="rounded-panel border border-border-subtle bg-panel p-panel">
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
        "min-h-control-sm shrink-0 rounded-control border px-3 text-small font-semibold transition-colors",
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
  const [downloadedOnly, setDownloadedOnly] = useState(false);
  const [notWatchedOnly, setNotWatchedOnly] = useState(false);
  const [statuses, setStatuses] = useState<WatchStatusFilter[]>([]);
  const [filtersOpen, setFiltersOpen] = useState(false);

  const { data, error, isLoading, refetch } = useQuery<LibraryResponse>({
    queryKey: [
      "library",
      kind,
      downloadedOnly,
      notWatchedOnly,
      statuses.join(","),
    ],
    queryFn: ({ signal }) =>
      listTitles(
        {
          kind: kind === "all" ? null : kind,
          has_files: downloadedOnly ? true : null,
          watched: notWatchedOnly ? false : null,
          status: statuses,
          sort: "added",
          limit: 100,
        },
        signal,
      ),
    staleTime: 30_000,
  });

  const filtered =
    kind !== "all" || downloadedOnly || notWatchedOnly || statuses.length > 0;
  const visibleStatuses = LIBRARY_STATUS_FILTERS.filter(
    (status) => filtersOpen || statuses.includes(status) || PRIMARY_STATUSES.includes(status),
  );

  function toggleStatus(status: WatchStatusFilter) {
    setNotWatchedOnly(false);
    setStatuses((current) =>
      current.includes(status)
        ? current.filter((value) => value !== status)
        : [...current, status],
    );
  }

  function toggleNotWatched() {
    setNotWatchedOnly((value) => {
      const next = !value;
      if (next) {
        setStatuses([]);
      }
      return next;
    });
  }

  return (
    <section className="relative flex flex-col gap-section">
      <header className="relative flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-h1 font-bold text-fg">Library</h1>
          <p className="text-small text-fg-muted">
            {data ? `${data.total} ${data.total === 1 ? "title" : "titles"}` : "Your saved titles"}
          </p>
        </div>
        <Link
          to="/search"
          className="inline-flex min-h-control-sm items-center rounded-control bg-accent px-3 text-small font-semibold text-canvas hover:bg-accent-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Search
        </Link>
      </header>

      <div className="flex items-center gap-2">
        <div
          id="library-filter-bar"
          role="group"
          aria-label="Library filters"
          className="flex min-w-0 flex-1 gap-2 overflow-x-auto whitespace-nowrap pb-1 [scrollbar-width:none]"
        >
          {KIND_OPTIONS.map((option) => (
            <FilterChip
              key={option.value}
              label={option.label}
              active={kind === option.value}
              onClick={() => setKind(option.value)}
            />
          ))}
          <FilterChip
            label="Downloaded"
            active={downloadedOnly}
            onClick={() => setDownloadedOnly((value) => !value)}
          />
          {visibleStatuses.map((status) => (
            <FilterChip
              key={status}
              label={WATCH_STATUS_LABELS[status]}
              active={statuses.includes(status)}
              onClick={() => toggleStatus(status)}
            />
          ))}
          <FilterChip
            label="Not watched"
            active={notWatchedOnly}
            onClick={toggleNotWatched}
          />
        </div>
        <button
          type="button"
          aria-expanded={filtersOpen}
          aria-controls="library-filter-bar"
          onClick={() => setFiltersOpen((value) => !value)}
          className="inline-flex min-h-control-sm shrink-0 items-center gap-1 rounded-control border border-border-subtle px-2 text-small font-semibold text-fg-muted hover:bg-hover hover:text-fg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {filtersOpen ? "Less" : "More"}
          <ChevronDownIcon
            className={`h-3.5 w-3.5 transition-transform ${filtersOpen ? "rotate-180" : ""}`}
          />
        </button>
      </div>

      <ContinueWatching />

      {error ? (
        <div className="rounded-panel border-l-2 border-danger bg-panel px-panel py-panel-compact">
          <p className="text-small text-danger">{error.message}</p>
          <button
            type="button"
            onClick={() => void refetch()}
            className="mt-2 min-h-control-sm rounded-control border border-border-strong px-3 text-small font-semibold text-fg hover:bg-hover"
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
            {notWatchedOnly
              ? "Nothing left unwatched"
              : statuses.length > 0
                ? "No titles with these watch statuses"
                : filtered
                  ? "No titles match these filters"
                  : "Library is empty"}
          </p>
          <p className="mx-auto mt-1 max-w-[48ch] text-small text-fg-muted">
            {notWatchedOnly
              ? "Every title matching the other filters is marked completed. Clear the filter or change a title's status."
              : statuses.length > 0
                ? "Watch status is your own local progress, separate from whether a file is available. Clear a status chip or set a status from a title page."
                : filtered
                  ? "Clear a filter or search for something new."
                  : "Search for a title to add it. Saved entries stay here even with no file."}
          </p>
          <Link
            to="/search"
            className="mt-4 inline-flex min-h-control-sm items-center rounded-control border border-border-strong px-3 text-small font-semibold text-fg hover:bg-hover"
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
