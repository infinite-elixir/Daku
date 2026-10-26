export type Kind = "movie" | "series" | "anime";
export type Availability = "none" | "partial" | "complete";
export type SortOrder = "added" | "title" | "year" | "kind" | "availability";

export interface SearchResult {
  source: string;
  source_id: string;
  kind: Kind;
  title: string;
  year: number | null;
  poster_url: string | null;
  id_mal?: number | null;
  format?: string | null;
  episode_count?: number | null;
  anime?: boolean;
  anilist_id?: number | null;
  classification_reason?: string | null;
}

export interface SearchResponse {
  query: string;
  kind: Kind | null;
  format: string | null;
  stale: boolean;
  results: SearchResult[];
}

export interface TitleSummary {
  id: string;
  kind: Kind;
  title: string;
  year: number | null;
  imdb_id: string | null;
  tmdb_id: string | null;
  tvdb_id: string | null;
  anilist_id: number | null;
  mal_id: number | null;
  overview: string | null;
  poster_url: string | null;
  folder_name: string | null;
  added_at: string;
  wanted: boolean;
  wanted_at: string | null;
  availability: Availability;
  saved_release_count: number;
}

export interface Episode {
  id: string;
  season: number;
  episode: number;
  label: string;
  absolute: number | null;
  name: string | null;
  air_date: string | null;
  status: string;
}

export interface SeasonInfo {
  library_season: number;
  provider_season: number;
  source: "cinemeta" | "tvdb" | "anilist" | string;
  anilist_id: number | null;
  episode_count: number;
  absolute_offset?: number;
}

export interface SeasonRebuildBody {
  source: "auto" | "imdb" | "tvdb" | "anilist";
  confirm?: boolean;
}

export interface SeasonIdentityBody {
  seasons: { library_season: number; anilist_id: number }[];
}

export interface SeasonRebuildResult {
  source: string;
  kept_episodes: number;
  renumbered: number;
  created: number;
  unmatched: number;
  files_at_risk: number;
}

export interface TitleDetail extends TitleSummary {
  episode_count: number;
  available_episode_count: number;
  episodes: Episode[];
  metadata_source?: string | null;
  metadata_synced_at?: string | null;
  metadata_state?: "empty" | "stale" | "ok";
  classification?: Classification;
  overrides?: TitleOverrides;
  seasons?: SeasonInfo[];
  rebuild?: SeasonRebuildResult;
}

export interface TitleMetadata {
  imdb_rating?: number | null;
  rt_rating?: number | null;
  metacritic_rating?: number | null;
  runtime_minutes?: number | null;
  genres?: string[];
  cast?: string[];
  directors?: string[];
  writers?: string[];
  studios?: string[];
  stale: boolean;
  sources?: string[];
}

export interface PreviewTitle {
  id: string | null;
  source: string;
  source_id: string;
  library_title_id: string | null;
  stale: boolean;
  kind: Kind;
  title: string;
  year: number | null;
  imdb_id: string | null;
  tmdb_id: string | null;
  tvdb_id: string | null;
  anilist_id: number | null;
  mal_id: number | null;
  overview: string | null;
  poster_url: string | null;
  folder_name: string | null;
  wanted: boolean;
  availability: Availability;
  saved_release_count: number;
  episode_count: number;
  available_episode_count: number;
  episodes: Episode[];
  imdb_rating?: number | null;
  rt_rating?: number | null;
  metacritic_rating?: number | null;
  runtime_minutes?: number | null;
  genres?: string[];
  cast?: string[];
  directors?: string[];
  writers?: string[];
  studios?: string[];
  sources?: string[];
  anime?: boolean;
  classification?: Classification;
  seasons?: SeasonInfo[];
}

export interface Classification {
  is_anime: boolean;
  confidence: "mapped" | "exact" | "none";
  reason: string;
  suggested_anilist_id: number | null;
}

export interface LibraryResponse {
  items: TitleSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface LibraryFilters {
  kind?: Kind | null;
  availability?: Availability | null;
  wanted?: boolean | null;
  q?: string | null;
  sort?: SortOrder;
  limit?: number;
  offset?: number;
}

export interface AddTitleBody {
  source: string;
  source_id: string;
  kind: Kind;
  title?: string | null;
  year?: number | null;
  poster_url?: string | null;
  wanted?: boolean;
  metadata_source?: "cinemeta" | "tvdb" | "anilist" | null;
  anime?: boolean;
  anilist_id?: number | null;
}

export interface PatchTitleBody {
  wanted?: boolean;
  folder_name?: string | null;
  kind?: Kind;
}

export type EditableField =
  | "kind"
  | "title"
  | "year"
  | "imdb_id"
  | "tvdb_id"
  | "anilist_id"
  | "metadata_source";

export interface SeasonOffset {
  provider_season: number;
  library_season: number;
}

export interface EpisodeOverride {
  id?: string;
  season: number;
  episode: number;
  target_season: number;
  target_episode: number;
  absolute: number | null;
  created_at?: string;
}

export interface TitleOverrides {
  title_id: string;
  fields: Partial<Record<EditableField, string | number | null>>;
  season_offsets: SeasonOffset[];
  episodes: EpisodeOverride[];
  provider_values: Partial<Record<EditableField, string | number | null>>;
}

export interface IdentityPatchBody {
  kind?: Kind;
  title?: string | null;
  year?: number | null;
  imdb_id?: string | null;
  tvdb_id?: string | null;
  anilist_id?: number | null;
  metadata_source?: "cinemeta" | "tvdb" | "anilist" | null;
  confirm?: boolean;
  force?: boolean;
}

export interface EpisodeMapBody {
  season_offsets: SeasonOffset[];
  episodes: Omit<EpisodeOverride, "id" | "created_at">[];
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(message: string, status: number, code: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    let message = `request failed: ${response.status}`;
    let code: string | null = null;
    try {
      const payload = (await response.json()) as { message?: string; code?: string };
      if (typeof payload.message === "string") {
        message = payload.message;
      }
      if (typeof payload.code === "string") {
        code = payload.code;
      }
    } catch {
      // Keep the status-based message when the body is not JSON.
    }
    throw new ApiError(message, response.status, code);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

function withQuery(path: string, params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") {
      continue;
    }
    search.set(key, String(value));
  }
  const encoded = search.toString();
  return encoded ? `${path}?${encoded}` : path;
}

export function searchTitles(
  params: { q: string; kind?: Kind | null; limit?: number },
  signal?: AbortSignal,
): Promise<SearchResponse> {
  return request<SearchResponse>(
    withQuery("/api/search", {
      q: params.q,
      kind: params.kind ?? null,
      limit: params.limit ?? 30,
    }),
    { signal },
  );
}

export function listTitles(filters: LibraryFilters = {}, signal?: AbortSignal): Promise<LibraryResponse> {
  return request<LibraryResponse>(
    withQuery("/api/titles", {
      kind: filters.kind ?? null,
      availability: filters.availability ?? null,
      wanted: filters.wanted ?? null,
      q: filters.q ?? null,
      sort: filters.sort ?? "added",
      limit: filters.limit ?? 100,
      offset: filters.offset ?? 0,
    }),
    { signal },
  );
}

export function getTitle(id: string, signal?: AbortSignal): Promise<TitleDetail> {
  return request<TitleDetail>(`/api/titles/${encodeURIComponent(id)}`, { signal });
}

export function getTitleMetadata(id: string, signal?: AbortSignal): Promise<TitleMetadata> {
  return request<TitleMetadata>(`/api/titles/${encodeURIComponent(id)}/metadata`, { signal });
}

export function addTitle(body: AddTitleBody): Promise<TitleDetail> {
  return request<TitleDetail>("/api/titles", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function getPreview(
  params: { source: string; source_id: string; kind: Kind },
  signal?: AbortSignal,
): Promise<PreviewTitle> {
  return request<PreviewTitle>(
    withQuery("/api/metadata/preview", {
      source: params.source,
      source_id: params.source_id,
      kind: params.kind,
    }),
    { signal },
  );
}

export function patchTitle(id: string, body: PatchTitleBody): Promise<TitleDetail> {
  return request<TitleDetail>(`/api/titles/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function deleteTitle(id: string, keepFiles = true): Promise<{ id: string; deleted: boolean }> {
  return request<{ id: string; deleted: boolean }>(
    withQuery(`/api/titles/${encodeURIComponent(id)}`, { keep_files: keepFiles }),
    { method: "DELETE" },
  );
}

export function getTitleOverrides(id: string, signal?: AbortSignal): Promise<TitleOverrides> {
  return request<TitleOverrides>(`/api/titles/${encodeURIComponent(id)}/overrides`, { signal });
}

export function patchTitleIdentity(
  id: string,
  body: IdentityPatchBody,
): Promise<TitleDetail & { overrides: TitleOverrides; refresh_error?: { code: string; message: string } }> {
  return request(`/api/titles/${encodeURIComponent(id)}/identity`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function putEpisodeMap(
  id: string,
  body: EpisodeMapBody,
): Promise<TitleDetail & { overrides: TitleOverrides }> {
  return request(`/api/titles/${encodeURIComponent(id)}/episode-map`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function deleteTitleOverride(
  id: string,
  field: EditableField,
): Promise<TitleDetail & { overrides: TitleOverrides }> {
  return request(
    `/api/titles/${encodeURIComponent(id)}/overrides/${encodeURIComponent(field)}`,
    { method: "DELETE" },
  );
}

export function rebuildTitleSeasons(
  id: string,
  body: SeasonRebuildBody,
): Promise<TitleDetail & { rebuild: SeasonRebuildResult }> {
  return request(`/api/titles/${encodeURIComponent(id)}/seasons/rebuild`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function putTitleSeasons(
  id: string,
  body: SeasonIdentityBody,
): Promise<TitleDetail & { rebuild: SeasonRebuildResult }> {
  return request(`/api/titles/${encodeURIComponent(id)}/seasons`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// ------------------------------------------------------------------ releases

export type ResolvedFrom = "torrentio" | "nyaa" | "manual";

export interface ReleaseHints {
  source: string | null;
  video_codec: string | null;
  audio_hint: string | null;
  languages: string[] | null;
  release_group: string | null;
  edition: string | null;
  resolved_from?: ResolvedFrom;
  category?: string | null;
  trusted?: boolean | null;
  remake?: boolean | null;
}

export interface ReleaseItem {
  info_hash: string;
  filename: string | null;
  quality: string | null;
  size_bytes: number | null;
  seeders: number | null;
  provider: string | null;
  magnet: string;
  metadata_json: ReleaseHints;
  release_id: string | null;
  is_saved: boolean;
  dead: boolean;
  saved_at: string | null;
  last_checked_at: string | null;
  resolved_from: ResolvedFrom;
}

export interface StoredRelease extends ReleaseItem {
  id: string;
  title_id: string;
  episode_id: string | null;
  last_seen_seeders: number | null;
  miss_count: number;
  fetched_at: string | null;
}

export interface ReleasesResponse {
  title_id: string;
  type: string;
  id: string;
  episode_id: string | null;
  stale: boolean;
  cached_at: string | null;
  sort: string;
  quality_filter: string[];
  providers: string[];
  defaults_source?: "request" | "settings" | "config";
  min_quality: string | null;
  limit: number;
  total: number;
  total_unfiltered: number;
  returned: number;
  duplicates_removed: number;
  quality_counts: Record<string, number>;
  sources?: string[];
  sources_degraded?: string[];
  releases: ReleaseItem[];
}

export interface ReleaseOptions {
  providers: string[];
  anime_sources?: string[];
  quality_labels: string[];
  min_qualities: string[];
  sorts: string[];
  defaults: {
    providers: string[];
    min_quality: string | null;
    sort: string;
    limit: number;
  };
  kind?: Kind;
  limit_steps: number[];
}

export interface RefreshReport {
  attempted: number;
  checked: number;
  found: number;
  missed: number;
  stale: boolean;
  reason: string | null;
}

export interface SavedReleasesResponse {
  title_id: string;
  total: number;
  episode_id: string | null;
  quality: string[];
  dead: boolean | null;
  refresh: RefreshReport;
  releases: StoredRelease[];
}

export interface CreateReleaseBody {
  magnet: string;
  title_id: string;
  episode_id?: string | null;
  filename?: string | null;
  provider?: string | null;
  quality?: string | null;
  size_bytes?: number | null;
  seeders?: number | null;
  metadata_json?: ReleaseHints | null;
  resolved_from?: ResolvedFrom | null;
  save?: boolean;
}

export interface DownloadRecord {
  id: string;
  release_id: string | null;
  source: string;
  status: string;
  engine: string;
  external_id: string;
  progress: number;
  downloaded_bytes: number;
  total_bytes: number | null;
  speed_bps: number | null;
  eta_seconds: number | null;
  save_path?: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  title_id: string | null;
  title: string | null;
  episode_id: string | null;
  episode_label: string | null;
  quality: string | null;
  filename: string | null;
}

export interface DownloadsResponse {
  items: DownloadRecord[];
  total: number;
}

export type DownloadAction = "pause" | "resume" | "cancel" | "retry";

export interface ReleaseListParams {
  season?: number | null;
  episode?: number | null;
  providers?: string[] | null;
  min_quality?: string | null;
  sort?: string | null;
  limit?: number | null;
  sources?: string[] | null;
}

export function listReleases(
  titleId: string,
  params: ReleaseListParams = {},
  signal?: AbortSignal,
): Promise<ReleasesResponse> {
  return request<ReleasesResponse>(
    withQuery(`/api/titles/${encodeURIComponent(titleId)}/releases`, {
      season: params.season ?? null,
      episode: params.episode ?? null,
      providers:
        params.providers && params.providers.length > 0 ? params.providers.join(",") : null,
      min_quality: params.min_quality ?? null,
      sort: params.sort ?? null,
      limit: params.limit ?? null,
      sources: params.sources && params.sources.length > 0 ? params.sources.join(",") : null,
    }),
    { signal },
  );
}

export function getReleaseOptions(kind?: Kind, signal?: AbortSignal): Promise<ReleaseOptions> {
  return request<ReleaseOptions>(
    kind && kind !== "movie" ? withQuery("/api/releases/options", { kind }) : "/api/releases/options",
    { signal },
  );
}

export interface ReleaseSettings {
  release: {
    providers: Record<Kind, string[]>;
    config_providers: Record<Kind, string[]>;
    min_quality: string | null;
    sort: "seeders" | "quality" | "size";
    limit: number;
  };
  sources: { database: boolean; config_keys: string[]; anime_sources: string[] };
}

export function getSettings(signal?: AbortSignal): Promise<ReleaseSettings> {
  return request<ReleaseSettings>("/api/settings", { signal });
}

export function patchSettings(body: Partial<Omit<ReleaseSettings["release"], "providers" | "config_providers">> & {
  providers?: Partial<Record<Kind, string[]>>;
  anime_sources?: string[];
}): Promise<ReleaseSettings> {
  return request<ReleaseSettings>("/api/settings", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ release: body }),
  });
}

export function listSavedReleases(
  titleId: string,
  params: { episode_id?: string | null; quality?: string | null; dead?: boolean | null } = {},
  signal?: AbortSignal,
): Promise<SavedReleasesResponse> {
  return request<SavedReleasesResponse>(
    withQuery(`/api/titles/${encodeURIComponent(titleId)}/releases/saved`, {
      episode_id: params.episode_id ?? null,
      quality: params.quality ?? null,
      dead: params.dead ?? null,
    }),
    { signal },
  );
}

export function createRelease(body: CreateReleaseBody): Promise<StoredRelease> {
  return request<StoredRelease>("/api/releases", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function saveRelease(id: string): Promise<StoredRelease> {
  return request<StoredRelease>(`/api/releases/${encodeURIComponent(id)}/save`, {
    method: "POST",
  });
}

export function unsaveRelease(id: string): Promise<StoredRelease> {
  return request<StoredRelease>(`/api/releases/${encodeURIComponent(id)}/save`, {
    method: "DELETE",
  });
}

export function refreshRelease(
  id: string,
): Promise<{ refreshed: boolean; found: boolean; release: StoredRelease }> {
  return request<{ refreshed: boolean; found: boolean; release: StoredRelease }>(
    `/api/releases/${encodeURIComponent(id)}/refresh`,
    { method: "POST" },
  );
}

export function startDownload(releaseId: string): Promise<DownloadRecord> {
  return request<DownloadRecord>("/api/downloads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ release_id: releaseId }),
  });
}

export function listDownloads(signal?: AbortSignal): Promise<DownloadsResponse> {
  return request<DownloadsResponse>("/api/downloads", { signal });
}

export function downloadAction(id: string, action: DownloadAction): Promise<DownloadRecord> {
  return request<DownloadRecord>(`/api/downloads/${encodeURIComponent(id)}/${action}`, {
    method: "POST",
  });
}
