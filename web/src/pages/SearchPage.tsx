import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";

import { KindTabs, type KindFilter } from "../components/KindTabs";
import { SearchResultCard } from "../components/SearchResultCard";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import {
  addTitle,
  listTitles,
  searchTitles,
  type AddTitleBody,
  type Kind,
  type SearchResult,
  type TitleSummary,
} from "../lib/api";

const GRID = "grid grid-cols-2 gap-4 sm:grid-cols-4 lg:grid-cols-6";
const SEARCH_LIMIT = 30;
const KINDS: Kind[] = ["movie", "series", "anime"];

function resultKey(result: Pick<SearchResult, "source" | "source_id">): string {
  return `${result.source}:${result.source_id}`;
}

/** IMDb ids are the identity for movies/series; AniList ids for anime. */
function identityKey(kind: Kind, sourceId: string | number | null): string | null {
  if (sourceId === null || sourceId === "") {
    return null;
  }
  return kind === "anime" ? `a:${sourceId}` : `i:${sourceId}`;
}

function libraryIndex(items: TitleSummary[] | undefined): Map<string, string> {
  const index = new Map<string, string>();
  for (const item of items ?? []) {
    const key =
      item.kind === "anime"
        ? identityKey("anime", item.anilist_id)
        : identityKey(item.kind, item.imdb_id);
    if (key) {
      index.set(key, item.id);
    }
  }
  return index;
}

function parseKind(value: string | null): KindFilter {
  return value !== null && KINDS.includes(value as Kind) ? (value as Kind) : "all";
}

function SkeletonGrid() {
  return (
    <div className={GRID} aria-hidden data-testid="search-skeleton">
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

export function SearchPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const rawQuery = searchParams.get("q") ?? "";
  const kind = parseKind(searchParams.get("kind"));
  const debounced = useDebouncedValue(rawQuery.trim(), 300);
  const queryClient = useQueryClient();
  const enabled = debounced.length > 0;

  const library = useQuery({
    queryKey: ["library", "search-lookup"],
    queryFn: ({ signal }) => listTitles({ limit: 200 }, signal),
    staleTime: 60_000,
  });
  const libraryIds = useMemo(() => libraryIndex(library.data?.items), [library.data]);

  const { data, error, isLoading, refetch } = useQuery({
    queryKey: ["search", debounced, kind],
    queryFn: ({ signal }) =>
      searchTitles(
        {
          q: debounced,
          kind: kind === "all" ? null : kind,
          limit: SEARCH_LIMIT,
        },
        signal,
      ),
    enabled,
    staleTime: 30_000,
  });

  const add = useMutation({
    mutationFn: (body: AddTitleBody) => addTitle(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["library"] });
    },
  });

  function updateParams(updates: { q?: string; kind?: KindFilter }) {
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        if (updates.q !== undefined) {
          if (updates.q.trim()) {
            next.set("q", updates.q);
          } else {
            next.delete("q");
          }
        }
        if (updates.kind !== undefined) {
          if (updates.kind === "all") {
            next.delete("kind");
          } else {
            next.set("kind", updates.kind);
          }
        }
        return next;
      },
      { replace: true },
    );
  }

  function handleAdd(result: SearchResult) {
    add.mutate({
      source: result.source,
      source_id: result.source_id,
      kind: result.kind,
      title: result.title,
      year: result.year,
      poster_url: result.poster_url,
      metadata_source: result.kind === "anime" ? "anilist" : "cinemeta",
      anime: result.anime,
      anilist_id: result.anilist_id,
    });
  }

  return (
    <section className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-h1 font-bold text-fg">Search</h1>
        <p className="text-small text-fg-muted">
          IMDb and AniList metadata. Adding a title never starts a download.
        </p>
      </header>

      <div className="relative">
        <svg
          viewBox="0 0 24 24"
          width="16"
          height="16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          aria-hidden
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-muted"
        >
          <circle cx="11" cy="11" r="6.5" />
          <path d="m16 16 4 4" />
        </svg>
        <input
          type="search"
          value={rawQuery}
          onChange={(event) => updateParams({ q: event.target.value })}
          placeholder="Search movies, series and anime"
          aria-label="Search titles"
          autoComplete="off"
          className="h-11 w-full rounded-control border border-border-subtle bg-canvas pl-9 pr-3 text-body text-fg placeholder:text-fg-comment focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30"
        />
      </div>

      <KindTabs value={kind} onChange={(next) => updateParams({ kind: next })} />

      {add.error ? (
        <p role="alert" className="rounded-panel border-l-2 border-danger bg-panel px-4 py-3 text-small text-danger">
          Could not add the title. {add.error.message}
        </p>
      ) : null}

      {!enabled ? (
        <div className="rounded-panel border border-border-subtle bg-panel px-6 py-10 text-center">
          <p className="text-h2 font-semibold tracking-tight text-fg">Search the catalogue</p>
          <p className="mx-auto mt-1 max-w-[48ch] text-small text-fg-muted">
            Pick a movie, series or anime. It lands in your library as Saved, with no file and no
            download.
          </p>
        </div>
      ) : error ? (
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
      ) : data && data.results.length === 0 ? (
        <div className="rounded-panel border border-border-subtle bg-panel px-6 py-10 text-center">
          <p className="text-h2 font-semibold tracking-tight text-fg">No results</p>
          <p className="mt-1 text-small text-fg-muted">Try a different spelling or another kind.</p>
        </div>
      ) : data ? (
        <>
          {data.stale ? (
            <p className="text-small text-warning">Showing cached results; the metadata source is unreachable.</p>
          ) : null}
          <div className={GRID}>
            {data.results.map((result) => {
              const key = resultKey(result);
              const pending = add.isPending && add.variables ? resultKey(add.variables) === key : false;
              return (
                <SearchResultCard
                  key={key}
                  result={result}
                  libraryId={libraryIds.get(identityKey(result.kind, result.source_id) ?? "") ?? null}
                  pending={pending}
                  onAdd={handleAdd}
                />
              );
            })}
          </div>
        </>
      ) : null}
    </section>
  );
}
