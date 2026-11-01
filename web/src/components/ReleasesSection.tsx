import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { findEpisode } from "../lib/episodes";
import { describeReleaseSources, releaseSourceChoices, releaseSourceLabel } from "../lib/releaseSources";
import {
  createRelease,
  getReleaseOptions,
  listReleases,
  listSavedReleases,
  saveRelease,
  startDownload,
  unsaveRelease,
  type Episode,
  type ReleaseItem,
  type TitleDetail,
} from "../lib/api";
import { CopyMagnet } from "./CopyMagnet";
import { EpisodeDownload } from "./EpisodeDownload";
import { MagnetFiles, looksLikeMagnet } from "./MagnetFiles";

type Tab = "find" | "saved";
export type ReleasesMode = "all" | "find" | "saved";

const PROVIDERS_STORAGE_KEY = "daku.releaseProviders.override";
const SORT_CHOICES = ["seeders", "quality", "size"] as const;
const MIN_QUALITY_CHOICES = ["480p", "576p", "720p", "1080p", "1440p", "2160p"] as const;
const SORT_LABELS: Record<string, string> = {
  seeders: "Seeders",
  quality: "Quality",
  size: "Size",
};

function SourceBadge({ release }: { release: ReleaseItem }) {
  const animeTosho =
    release.provider === "animetosho" ||
    release.provider === "nyaa" ||
    release.resolved_from === "animetosho";
  const source = animeTosho ? "animetosho" : release.provider || "torrentio";
  const label = releaseSourceLabel(source);
  return (
    <span
      aria-label={`Source ${label}`}
      className="rounded-full bg-fg-muted/10 px-1.5 py-0.5 text-micro"
    >
      {label.toUpperCase()}
    </span>
  );
}

function PackBadge({ release }: { release: ReleaseItem }) {
  if (!release.is_pack) {
    return null;
  }
  const label = release.pack_label ? `Pack ${release.pack_label}` : "Pack";
  return (
    <span
      title="This release contains several episodes. Downloading it fetches only the selected episode's file."
      className="rounded-full bg-accent/15 px-1.5 py-0.5 text-micro font-medium text-accent"
    >
      {label.toUpperCase()}
    </span>
  );
}

function storage(): Storage | null {
  try {
    return window.localStorage ?? null;
  } catch {
    return null;
  }
}

function loadProviderSelection(kind: string): string[] | null {
  try {
    const target = storage();
    const raw =
      target?.getItem(`${PROVIDERS_STORAGE_KEY}.${kind}`) ??
      target?.getItem("daku.releaseProviders");
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) {
      return null;
    }
    const providers = parsed
      .filter((value): value is string => typeof value === "string")
      .map((value) => value.trim().toLowerCase())
      .filter(Boolean);
    return providers.length > 0 ? [...new Set(providers)] : null;
  } catch {
    return null;
  }
}

function storeProviderSelection(kind: string, providers: string[] | null): void {
  try {
    const target = storage();
    if (!target) {
      return;
    }
    if (providers === null) {
      target.removeItem(`${PROVIDERS_STORAGE_KEY}.${kind}`);
      target.removeItem("daku.releaseProviders");
    } else {
      target.setItem(`${PROVIDERS_STORAGE_KEY}.${kind}`, JSON.stringify(providers));
    }
  } catch {
    // Private-mode storage failures must not block release search.
  }
}

const QUALITY_CHOICES = [
  "2160p",
  "1440p",
  "1080p",
  "720p",
  "576p",
  "480p",
  "cam",
  "scr",
  "ts",
  "unknown",
];

const DEAD_PILL =
  "inline-flex items-center rounded-full bg-danger/15 px-2 py-0.5 text-micro font-medium text-danger";
const SAVED_PILL =
  "inline-flex items-center rounded-full bg-accent/15 px-2 py-0.5 text-micro font-medium text-accent";

function formatSize(bytes: number | null): string {
  if (bytes === null || bytes <= 0) {
    return "size unknown";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

function formatChecked(value: string | null): string {
  if (!value) {
    return "never checked";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function hintsLine(release: ReleaseItem): string {
  const hints = release.metadata_json ?? {};
  const languages = Array.isArray(hints.languages) ? hints.languages.join("/") : null;
  return [hints.source, hints.video_codec, hints.audio_hint, languages, hints.edition]
    .filter((value): value is string => Boolean(value))
    .join(" · ");
}

function seasonNumbers(episodes: Episode[]): number[] {
  return [...new Set(episodes.map((episode) => episode.season))].sort((a, b) => a - b);
}

export function ReleasesSection({
  title,
  season = null,
  episode = null,
  mode = "all",
}: {
  title: TitleDetail;
  season?: number | null;
  episode?: number | null;
  /**
   * `find` renders only Torrentio results (used inside an episode card).
   * `saved` renders saved releases plus the magnet form (below the episodes).
   * `all` keeps the original tabs for movies.
   */
  mode?: ReleasesMode;
}) {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>(title.saved_release_count > 0 ? "saved" : "find");
  const [magnet, setMagnet] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [qualityFilter, setQualityFilter] = useState("");
  const [hideDead, setHideDead] = useState(false);
  const [minQuality, setMinQuality] = useState<string | null>(null);
  const [sortMode, setSortMode] = useState("");
  const [providers, setProviders] = useState<string[] | null>(() => loadProviderSelection(title.kind));
  const [providerHint, setProviderHint] = useState<string | null>(null);
  const [limitOverride, setLimitOverride] = useState<number | null>(null);
  const [sources, setSources] = useState<string[] | null>(null);

  const isMovie = title.kind === "movie";
  const showFind = mode !== "saved";
  const showSaved = mode !== "find";
  const showTabs = mode === "all";
  const activeTab: Tab = mode === "find" ? "find" : mode === "saved" ? "saved" : tab;
  const seasons = useMemo(() => seasonNumbers(title.episodes), [title.episodes]);
  const selectedEpisode = useMemo(
    () => (isMovie ? null : findEpisode(title.episodes, season, episode)),
    [title.episodes, season, episode, isMovie],
  );
  const episodeLabels = useMemo(
    () => new Map(title.episodes.map((item) => [item.id, item.label])),
    [title.episodes],
  );
  const ready = isMovie || selectedEpisode !== null;

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["title", title.id] });
    void queryClient.invalidateQueries({ queryKey: ["library"] });
    void queryClient.invalidateQueries({ queryKey: ["releases", title.id] });
    void queryClient.invalidateQueries({ queryKey: ["saved-releases", title.id] });
  };

  const optionsQuery = useQuery({
    queryKey: ["release-options", title.kind],
    queryFn: ({ signal }) => getReleaseOptions(title.kind, signal),
    enabled: showFind && activeTab === "find",
    staleTime: Infinity,
  });

  const options = optionsQuery.data;
  const availableProviders = options?.providers ?? [];
  const defaultProviders =
    options?.defaults?.providers && options.defaults.providers.length > 0
      ? options.defaults.providers
      : availableProviders;
  const effectiveProviders = providers ?? defaultProviders;
  const effectiveMinQuality =
    minQuality !== null ? minQuality || null : options?.defaults?.min_quality || null;
  const effectiveSort = sortMode || options?.defaults?.sort || "seeders";
  const limitSteps = options?.limit_steps?.length ? options.limit_steps : [20, 50, 100, 200];
  const effectiveLimit = limitOverride ?? options?.defaults?.limit ?? 20;
  const effectiveSources = sources ?? (options?.anime_sources ?? ["torrentio", "animetosho"]);
  const displaySources =
    title.kind !== "anime" ? ["torrentio"] : options || sources ? effectiveSources : [];
  const sourceChoices = releaseSourceChoices(effectiveSources);

  const findQuery = useQuery({
    queryKey: [
      "releases",
      title.id,
      season,
      episode,
      effectiveProviders.join(","),
      effectiveMinQuality,
      effectiveSort,
      effectiveLimit,
      effectiveSources.join(","),
    ],
    queryFn: ({ signal }) =>
      listReleases(
        title.id,
        {
          ...(isMovie ? {} : { season: season ?? undefined, episode: episode ?? undefined }),
          providers: providers,
          sources: title.kind === "anime" ? effectiveSources : null,
          min_quality: effectiveMinQuality,
          sort: effectiveSort,
          limit: effectiveLimit,
        },
        signal,
      ),
    enabled: showFind && activeTab === "find" && ready,
    staleTime: 60_000,
    // Show more changes the query key; keep the current rows mounted while the
    // larger page loads so the list and its scroll position do not jump.
    placeholderData: keepPreviousData,
  });

  const savedQuery = useQuery({
    queryKey: ["saved-releases", title.id, qualityFilter, hideDead],
    queryFn: ({ signal }) =>
      listSavedReleases(
        title.id,
        { quality: qualityFilter || null, dead: hideDead ? false : null },
        signal,
      ),
    enabled: showSaved && activeTab === "saved",
    staleTime: 60_000,
  });

  const saveMutation = useMutation({
    mutationFn: (release: ReleaseItem) => {
      if (release.release_id) {
        return release.is_saved ? unsaveRelease(release.release_id) : saveRelease(release.release_id);
      }
      return createRelease({
        magnet: release.magnet,
        title_id: title.id,
        episode_id: isMovie ? null : (selectedEpisode?.id ?? null),
        filename: release.filename,
        provider: release.provider,
        quality: release.quality,
        size_bytes: release.size_bytes,
        seeders: release.seeders,
        metadata_json: release.metadata_json,
        resolved_from: release.resolved_from,
        save: true,
      });
    },
    onSuccess: (saved) => {
      setNotice(saved.is_saved ? "Saved. No download was started." : "Removed from Saved.");
      invalidate();
    },
  });

  const downloadMutation = useMutation({
    mutationFn: async (release: ReleaseItem) => {
      let releaseId = release.release_id;
      if (!releaseId) {
        const stored = await createRelease({
          magnet: release.magnet,
          title_id: title.id,
          episode_id: isMovie ? null : (selectedEpisode?.id ?? null),
          filename: release.filename,
          provider: release.provider,
          quality: release.quality,
          size_bytes: release.size_bytes,
          seeders: release.seeders,
          metadata_json: release.metadata_json,
          resolved_from: release.resolved_from,
          save: false,
        });
        releaseId = stored.id;
      }
      return startDownload(releaseId);
    },
    onSuccess: () => {
      setNotice("Queued for download.");
      invalidate();
    },
  });

  const manualMutation = useMutation({
    mutationFn: () =>
      createRelease({
        magnet: magnet.trim(),
        title_id: title.id,
        episode_id: isMovie ? null : (selectedEpisode?.id ?? null),
        save: true,
      }),
    onSuccess: () => {
      setMagnet("");
      setNotice("Magnet saved. No download was started.");
      invalidate();
    },
  });

  const activeError = saveMutation.error ?? downloadMutation.error ?? manualMutation.error;
  const counts = findQuery.data?.quality_counts ?? {};
  const countLabel = Object.entries(counts)
    .filter(([, count]) => count > 0)
    .map(([quality, count]) => `${quality} ${count}`)
    .join(" · ");
  const nextLimit = limitSteps.find((step) => step > effectiveLimit) ?? effectiveLimit;
  const canShowMore =
    findQuery.data !== undefined &&
    findQuery.data.returned < findQuery.data.total &&
    nextLimit > effectiveLimit;
  const filtersActive = providers !== null || effectiveMinQuality !== null;

  function toggleProvider(provider: string) {
    const next = effectiveProviders.includes(provider)
      ? effectiveProviders.filter((value) => value !== provider)
      : [...effectiveProviders, provider];
    if (next.length === 0) {
      setProviderHint("Choose at least one provider.");
      return;
    }
    setProviderHint(null);
    const isDefault =
      next.length === defaultProviders.length &&
      defaultProviders.every((value) => next.includes(value));
    const selection = isDefault ? null : next;
    setProviders(selection);
    storeProviderSelection(title.kind, selection);
    setLimitOverride(null);
  }
  function toggleSource(source: string) {
    const next = effectiveSources.includes(source)
      ? effectiveSources.filter((value) => value !== source)
      : [...effectiveSources, source];
    if (next.length > 0) setSources(next);
  }

  function resetFindFilters() {
    setMinQuality(null);
    setSortMode("");
    setProviders(null);
    setProviderHint(null);
    setLimitOverride(null);
    storeProviderSelection(title.kind, null);
    storage()?.removeItem("daku.releaseProviders");
  }

  function showMore() {
    if (canShowMore) {
      setLimitOverride(nextLimit);
    }
  }

  async function ensureReleaseId(release: ReleaseItem): Promise<string> {
    if (release.release_id) {
      return release.release_id;
    }
    const stored = await createRelease({
      magnet: release.magnet,
      title_id: title.id,
      episode_id: isMovie ? null : (selectedEpisode?.id ?? null),
      filename: release.filename,
      provider: release.provider,
      quality: release.quality,
      size_bytes: release.size_bytes,
      seeders: release.seeders,
      metadata_json: release.metadata_json,
      resolved_from: release.resolved_from,
      save: false,
    });
    return stored.id;
  }

  return (
    <section className="flex flex-col gap-4">
      {mode === "find" ? null : (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-h2 font-semibold tracking-tight text-fg">
            {mode === "saved" ? "Saved releases" : "Releases"}
          </h2>
          {showTabs ? (
            <div className="flex items-center gap-1 rounded-control border border-border-subtle bg-panel p-1">
              <button
                type="button"
                aria-pressed={tab === "find"}
                onClick={() => setTab("find")}
                className={[
                  "min-h-control-sm rounded-control px-3 text-small font-semibold",
                  tab === "find" ? "bg-accent/15 text-accent" : "text-fg-muted hover:bg-hover",
                ].join(" ")}
              >
                Find releases
              </button>
              <button
                type="button"
                aria-pressed={tab === "saved"}
                onClick={() => setTab("saved")}
                className={[
                  "min-h-control-sm rounded-control px-3 text-small font-semibold",
                  tab === "saved" ? "bg-accent/15 text-accent" : "text-fg-muted hover:bg-hover",
                ].join(" ")}
              >
                Saved ({title.saved_release_count})
              </button>
            </div>
          ) : null}
        </div>
      )}

      {!isMovie && mode === "all" ? (
        <p className="flex flex-wrap items-center gap-2 rounded-panel border border-border-subtle bg-panel px-3 py-2 text-small text-fg-muted">
          <span>Searching releases for</span>
          <span className="rounded-full bg-accent/15 px-2 py-0.5 font-mono text-small font-semibold tabular-nums text-accent">
            {selectedEpisode?.label ?? "no episode"}
          </span>
          {selectedEpisode?.name ? <span className="text-fg-comment">· {selectedEpisode.name}</span> : null}
        </p>
      ) : null}

      {notice ? (
        <p role="status" className="text-small text-success">
          {notice}
        </p>
      ) : null}
      {activeError ? (
        <p role="alert" className="text-small text-danger">
          {activeError.message}
        </p>
      ) : null}

      {showFind && activeTab === "find" ? (
        <>
          {!isMovie && mode === "all" && seasons.length === 0 ? (
            <p className="rounded-panel border border-border-subtle bg-panel px-panel py-gutter text-small text-fg-muted">
              No episodes recorded yet, so there is nothing to search for.
            </p>
          ) : null}

          <div
            aria-label="Release filters"
            className="flex flex-wrap items-center gap-3 rounded-panel border border-border-subtle bg-panel px-3 py-2"
          >
            <label className="flex items-center gap-2 text-small text-fg-muted">
              Minimum quality
              <select
                aria-label="Minimum quality"
                value={minQuality !== null ? minQuality : options?.defaults?.min_quality || ""}
                onChange={(event) => {
                  setMinQuality(event.target.value);
                  setLimitOverride(null);
                }}
                className="min-h-control rounded-control border border-border-strong bg-canvas px-2 text-small text-fg"
              >
                <option value="">Any</option>
                {MIN_QUALITY_CHOICES.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>

            <label className="flex items-center gap-2 text-small text-fg-muted">
              Sort
              <select
                aria-label="Release sort"
                value={effectiveSort}
                onChange={(event) => {
                  setSortMode(event.target.value);
                  setLimitOverride(null);
                }}
                className="min-h-control rounded-control border border-border-strong bg-canvas px-2 text-small text-fg"
              >
                {SORT_CHOICES.map((value) => (
                  <option key={value} value={value}>
                    {SORT_LABELS[value]}
                  </option>
                ))}
              </select>
            </label>

            <details className="relative">
              <summary className="min-h-control-sm cursor-pointer list-none rounded-control border border-border-strong px-3 py-1.5 text-small font-semibold text-fg hover:bg-hover">
                Providers ({providers === null ? "default" : effectiveProviders.length})
              </summary>
              <div className="absolute right-0 z-10 mt-1 flex w-56 flex-col gap-1 rounded-panel border border-border-strong bg-panel p-3 shadow-lg">
                <button
                  type="button"
                  onClick={() => {
                    setProviders(null);
                    setProviderHint(null);
                    setLimitOverride(null);
                    storeProviderSelection(title.kind, null);
                    storage()?.removeItem("daku.releaseProviders");
                  }}
                  className="min-h-control-sm rounded-control px-2 text-left text-small font-semibold text-accent hover:bg-hover"
                >
                  Use defaults
                </button>
                {availableProviders.length === 0 ? (
                  <p className="text-micro text-fg-comment">Loading providers…</p>
                ) : null}
                {availableProviders.map((provider) => (
                  <label
                    key={provider}
                    className="flex items-center gap-2 rounded-control px-2 py-1 text-small text-fg-muted hover:bg-hover"
                  >
                    <input
                      type="checkbox"
                      aria-label={`Provider ${provider}`}
                      checked={effectiveProviders.includes(provider)}
                      onChange={() => toggleProvider(provider)}
                    />
                    {provider}
                  </label>
                ))}
                {title.kind === "anime" ? (
                  <>
                    <p className="mt-2 border-t border-border-subtle pt-2 text-micro font-semibold text-fg-muted">Sources</p>
                    {sourceChoices.map((source) => (
                      <label key={source.id} className="flex items-center gap-2 rounded-control px-2 py-1 text-small text-fg-muted hover:bg-hover">
                        <input type="checkbox" aria-label={`Source ${source.id}`} checked={effectiveSources.includes(source.id)} onChange={() => toggleSource(source.id)} />
                        {source.number} · {source.label}
                      </label>
                    ))}
                  </>
                ) : null}
                {providerHint ? (
                  <p role="status" className="text-micro text-warning">
                    {providerHint}
                  </p>
                ) : null}
              </div>
            </details>

            <button
              type="button"
              onClick={resetFindFilters}
              disabled={!filtersActive && limitOverride === null && sortMode === ""}
              className="min-h-control-sm rounded-control border border-border-strong px-3 text-small font-semibold text-fg hover:bg-hover disabled:opacity-40"
            >
              Reset
            </button>
          </div>

          {findQuery.isLoading ? (
            <p className="text-small text-fg-muted">{describeReleaseSources(displaySources)}</p>
          ) : null}
          {findQuery.error ? (
            <p role="alert" className="text-small text-danger">
              {findQuery.error.message}
            </p>
          ) : null}

          {findQuery.data ? (
            <>
              <div className="flex flex-wrap items-center gap-2 text-small text-fg-comment">
                <span>
                  Showing {findQuery.data.returned} of {findQuery.data.total}
                  {findQuery.data.total_unfiltered > findQuery.data.total
                    ? ` (${findQuery.data.total_unfiltered} before filters)`
                    : ""}
                </span>
                {countLabel ? <span>· {countLabel}</span> : null}
                {findQuery.data.sources_degraded?.map((source) => (
                  <span key={source} className="rounded-full bg-warning/15 px-2 py-0.5 text-warning">
                    {releaseSourceLabel(source)} unavailable.
                  </span>
                ))}
                {findQuery.data.stale ? (
                  <span className="rounded-full bg-warning/15 px-2 py-0.5 text-warning">
                    Showing cached results
                  </span>
                ) : null}
              </div>
              {findQuery.data.releases.length === 0 ? (
                <div className="flex flex-col gap-2 rounded-panel border border-border-subtle bg-panel px-panel py-gutter">
                  <p className="text-small text-fg-muted">
                    {filtersActive
                      ? `No releases match${
                          effectiveMinQuality ? ` a minimum of ${effectiveMinQuality}` : ""
                        }${
                          providers !== null
                            ? `${effectiveMinQuality ? " and" : ""} ${
                                effectiveProviders.join(", ") || "the selected providers"
                              }`
                            : ""
                        }.`
                      : "No releases matched. Try another episode or clear the quality filter."}
                  </p>
                  {filtersActive ? (
                    <button
                      type="button"
                      onClick={resetFindFilters}
                      className="min-h-control-sm w-fit rounded-control border border-border-strong px-3 text-small font-semibold text-fg hover:bg-hover"
                    >
                      Reset filters
                    </button>
                  ) : null}
                </div>
              ) : (
                <ul
                  className={[
                    "divide-y divide-border-subtle rounded-panel border border-border-subtle bg-panel",
                    mode === "find" ? "max-h-[22rem] overflow-y-auto" : "",
                  ].join(" ")}
                >
                  {findQuery.data.releases.map((release) => (
                    <li
                      key={release.info_hash}
                      className="flex flex-col gap-3 px-panel py-row"
                    >
                      <div className="flex flex-wrap items-center gap-3">
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-body font-semibold text-fg">
                            {release.filename ?? release.info_hash}
                          </p>
                          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-small text-fg-muted">
                            <span>{release.quality ?? "unknown"}</span>
                            <span>· {formatSize(release.size_bytes)}</span>
                            <span>· {release.seeders ?? 0} seeders</span>
                            <SourceBadge release={release} />
                            <PackBadge release={release} />
                          </p>
                          {hintsLine(release) ? (
                            <p className="mt-0.5 text-micro text-fg-comment">{hintsLine(release)}</p>
                          ) : null}
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                          <CopyMagnet magnet={release.magnet} compact />
                          <button
                            type="button"
                            aria-pressed={release.is_saved}
                            aria-label={`${release.is_saved ? "Unsave" : "Save"} ${
                              release.filename ?? release.info_hash
                            }`}
                            disabled={saveMutation.isPending}
                            onClick={() => saveMutation.mutate(release)}
                            className={[
                              "flex h-9 w-9 items-center justify-center rounded-control border text-title disabled:opacity-40",
                              release.is_saved
                                ? "border-accent/50 bg-accent/15 text-accent"
                                : "border-border-strong text-fg-muted hover:bg-hover",
                            ].join(" ")}
                          >
                            {release.is_saved ? "★" : "☆"}
                          </button>
                          <button
                            type="button"
                            aria-label={`Download ${release.filename ?? release.info_hash}`}
                            disabled={downloadMutation.isPending}
                            onClick={() => downloadMutation.mutate(release)}
                            className="min-h-control-sm rounded-control border border-border-strong px-3 text-small font-semibold text-fg hover:bg-hover disabled:opacity-40"
                          >
                            Download
                          </button>
                        </div>
                      </div>
                      {!isMovie && selectedEpisode ? (
                        <EpisodeDownload
                          release={release}
                          episodeId={selectedEpisode.id}
                          episodeLabel={selectedEpisode.label}
                          ensureRelease={() => ensureReleaseId(release)}
                          onQueued={(message) => {
                            setNotice(message);
                            invalidate();
                          }}
                        />
                      ) : null}
                      <MagnetFiles
                        magnet={release.magnet}
                        releaseId={release.release_id}
                        episodeId={isMovie ? null : (selectedEpisode?.id ?? null)}
                        onQueued={(message) => {
                          setNotice(message);
                          invalidate();
                        }}
                      />
                    </li>
                  ))}
                </ul>
              )}
              {findQuery.data.releases.length > 0 ? (
                <div className="flex items-center justify-between gap-2">
                  <span className="text-small text-fg-comment">
                    {findQuery.data.duplicates_removed > 0
                      ? `${findQuery.data.duplicates_removed} duplicate(s) collapsed`
                      : ""}
                  </span>
                  <button
                    type="button"
                    onClick={showMore}
                    disabled={!canShowMore}
                    className="min-h-control-sm rounded-control border border-border-strong px-3 text-small font-semibold text-fg hover:bg-hover disabled:opacity-40"
                  >
                    Show more
                  </button>
                </div>
              ) : null}
            </>
          ) : null}
        </>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-small text-fg-muted">
              Quality
              <select
                value={qualityFilter}
                onChange={(event) => setQualityFilter(event.target.value)}
                className="min-h-control rounded-control border border-border-strong bg-canvas px-2 text-small text-fg"
              >
                <option value="">All</option>
                {QUALITY_CHOICES.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-2 text-small text-fg-muted">
              <input
                type="checkbox"
                checked={hideDead}
                onChange={(event) => setHideDead(event.target.checked)}
              />
              Hide dead
            </label>
            {savedQuery.data ? (
              <span className="text-small text-fg-comment">
                {savedQuery.data.total} saved
                {savedQuery.data.refresh.found > 0
                  ? ` · ${savedQuery.data.refresh.found} re-checked`
                  : ""}
              </span>
            ) : null}
          </div>

          {savedQuery.isLoading ? (
            <p className="text-small text-fg-muted">Loading saved releases…</p>
          ) : null}
          {savedQuery.error ? (
            <p role="alert" className="text-small text-danger">
              {savedQuery.error.message}
            </p>
          ) : null}
          {savedQuery.data && savedQuery.data.releases.length === 0 ? (
            <p className="rounded-panel border border-border-subtle bg-panel px-panel py-gutter text-small text-fg-muted">
              Nothing saved yet. Star a release or paste a magnet to keep it for later.
            </p>
          ) : null}

          {savedQuery.data && savedQuery.data.releases.length > 0 ? (
            <ul className="divide-y divide-border-subtle rounded-panel border border-border-subtle bg-panel">
              {savedQuery.data.releases.map((release) => (
                <li key={release.id} className="flex flex-col gap-3 px-panel py-row">
                  <div className="flex flex-wrap items-center gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-body font-semibold text-fg">
                        {release.filename ?? release.info_hash}
                      </p>
                      <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-small text-fg-muted">
                        <span>{release.quality ?? "unknown"}</span>
                        <span>· {formatSize(release.size_bytes)}</span>
                        <span>· {release.seeders ?? 0} seeders</span>
                        <SourceBadge release={release} />
                      </p>
                      <p className="mt-0.5 flex flex-wrap items-center gap-2 text-micro text-fg-comment">
                        <span>Checked {formatChecked(release.last_checked_at)}</span>
                        {release.dead ? <span className={DEAD_PILL}>Dead</span> : null}
                        {release.resolved_from === "manual" ? (
                          <span className={SAVED_PILL}>Manual</span>
                        ) : null}
                        {release.episode_id ? (
                          <span>{episodeLabels.get(release.episode_id) ?? "episode"}</span>
                        ) : null}
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <CopyMagnet magnet={release.magnet} compact />
                      {release.dead ? (
                        <button
                          type="button"
                          onClick={() => {
                            if (showTabs) {
                              setTab("find");
                            } else {
                              document
                                .getElementById("episodes-heading")
                                ?.scrollIntoView?.({ behavior: "smooth", block: "start" });
                            }
                          }}
                          className="min-h-control-sm rounded-control border border-warning/50 px-3 text-small font-semibold text-warning hover:bg-warning/10"
                        >
                          Find another
                        </button>
                      ) : null}
                      <button
                        type="button"
                        aria-pressed={release.is_saved}
                        aria-label={`Unsave ${release.filename ?? release.info_hash}`}
                        disabled={saveMutation.isPending}
                        onClick={() => saveMutation.mutate(release)}
                        className="flex h-9 w-9 items-center justify-center rounded-control border border-accent/50 bg-accent/15 text-title text-accent disabled:opacity-40"
                      >
                        ★
                      </button>
                      <button
                        type="button"
                        aria-label={`Download ${release.filename ?? release.info_hash}`}
                        disabled={downloadMutation.isPending}
                        onClick={() => downloadMutation.mutate(release)}
                        className="min-h-control-sm rounded-control border border-border-strong px-3 text-small font-semibold text-fg hover:bg-hover disabled:opacity-40"
                      >
                        Download
                      </button>
                    </div>
                  </div>
                  {!isMovie && selectedEpisode ? (
                    <EpisodeDownload
                      release={release}
                      episodeId={selectedEpisode.id}
                      episodeLabel={selectedEpisode.label}
                      ensureRelease={() => ensureReleaseId(release)}
                      onQueued={(message) => {
                        setNotice(message);
                        invalidate();
                      }}
                    />
                  ) : null}
                  <MagnetFiles
                    magnet={release.magnet}
                    releaseId={release.id}
                    episodeId={isMovie ? null : (selectedEpisode?.id ?? null)}
                    onQueued={(message) => {
                      setNotice(message);
                      invalidate();
                    }}
                  />
                </li>
              ))}
            </ul>
          ) : null}
        </>
      )}

      {mode !== "find" ? (
        <form
          className="flex flex-col gap-2 rounded-panel border border-border-subtle bg-panel px-panel py-row"
          onSubmit={(event) => {
            event.preventDefault();
            if (magnet.trim() && ready) {
              manualMutation.mutate();
            }
          }}
        >
          <label htmlFor="manual-magnet" className="text-small font-semibold text-fg">
            Paste a magnet
          </label>
          <p className="text-micro text-fg-comment">
            {isMovie
              ? "Saved against this title. Parsing happens on the server; nothing is downloaded until you press Download."
              : ready
                ? "Saved against the open episode. Parsing happens on the server; nothing is downloaded until you press Download."
                : "Open an episode above to choose where this magnet belongs."}
          </p>
          <div className="flex flex-wrap gap-2">
            <input
              id="manual-magnet"
              value={magnet}
              onChange={(event) => setMagnet(event.target.value)}
              placeholder="magnet:?xt=urn:btih:…"
              disabled={!ready}
              className="min-h-control-sm min-w-0 flex-1 rounded-control border border-border-strong bg-canvas px-3 text-small text-fg placeholder:text-fg-comment disabled:opacity-60"
            />
            <button
              type="submit"
              disabled={!magnet.trim() || !ready || manualMutation.isPending}
              className="min-h-control-sm rounded-control border border-border-strong px-3 text-small font-semibold text-fg hover:bg-hover disabled:opacity-40"
            >
              {manualMutation.isPending ? "Saving…" : "Save magnet"}
            </button>
          </div>
          {looksLikeMagnet(magnet) ? (
            <MagnetFiles magnet={magnet.trim()} buttonLabel="Inspect" />
          ) : magnet.trim() && ready ? (
            <p className="text-micro text-fg-comment">
              Add exactly one btih info hash to inspect this magnet.
            </p>
          ) : null}
        </form>
      ) : null}
    </section>
  );
}
