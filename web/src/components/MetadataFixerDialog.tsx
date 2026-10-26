import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import {
  ApiError,
  deleteTitleOverride,
  getTitleOverrides,
  patchTitleIdentity,
  putEpisodeMap,
  putTitleSeasons,
  rebuildTitleSeasons,
  type EditableField,
  type EpisodeOverride,
  type IdentityPatchBody,
  type Kind,
  type SeasonOffset,
  type TitleDetail,
  type TitleOverrides,
} from "../lib/api";

const KINDS: { value: Kind; label: string }[] = [
  { value: "movie", label: "Movie" },
  { value: "series", label: "TV" },
  { value: "anime", label: "Anime" },
];

const SOURCES = [
  { value: "", label: "Provider default" },
  { value: "cinemeta", label: "IMDb / Cinemeta" },
  { value: "tvdb", label: "TheTVDB" },
  { value: "anilist", label: "AniList" },
];

interface OffsetRow {
  provider_season: string;
  library_season: string;
}

interface EpisodeRow {
  season: string;
  episode: string;
  target_season: string;
  target_episode: string;
  absolute: string;
}

interface FormState {
  kind: Kind;
  title: string;
  year: string;
  imdb_id: string;
  tvdb_id: string;
  anilist_id: string;
  metadata_source: string;
  offsets: OffsetRow[];
  episodes: EpisodeRow[];
}

function initialState(title: TitleDetail, overrides: TitleOverrides | undefined): FormState {
  const fields = overrides?.fields ?? {};
  const text = (field: EditableField, fallback: string | number | null | undefined) => {
    const value = field in fields ? fields[field] : fallback;
    return value === null || value === undefined ? "" : String(value);
  };
  return {
    kind: (fields.kind as Kind | undefined) ?? title.kind,
    title: text("title", title.title),
    year: text("year", title.year),
    imdb_id: text("imdb_id", title.imdb_id),
    tvdb_id: text("tvdb_id", title.tvdb_id),
    anilist_id: text("anilist_id", title.anilist_id),
    metadata_source: text("metadata_source", title.metadata_source ?? "",
    ),
    offsets: (overrides?.season_offsets ?? []).map((offset) => ({
      provider_season: String(offset.provider_season),
      library_season: String(offset.library_season),
    })),
    episodes: (overrides?.episodes ?? []).map((episode) => ({
      season: String(episode.season),
      episode: String(episode.episode),
      target_season: String(episode.target_season),
      target_episode: String(episode.target_episode),
      absolute: episode.absolute === null || episode.absolute === undefined ? "" : String(episode.absolute),
    })),
  };
}

function identityChanges(initial: FormState, form: FormState): IdentityPatchBody {
  const changes: IdentityPatchBody = {};
  if (form.kind !== initial.kind) changes.kind = form.kind;
  if (form.title !== initial.title) changes.title = form.title;
  if (form.year !== initial.year) changes.year = form.year === "" ? null : Number(form.year);
  if (form.imdb_id !== initial.imdb_id) changes.imdb_id = form.imdb_id || null;
  if (form.tvdb_id !== initial.tvdb_id) changes.tvdb_id = form.tvdb_id || null;
  if (form.anilist_id !== initial.anilist_id) {
    changes.anilist_id = form.anilist_id === "" ? null : Number(form.anilist_id);
  }
  if (form.kind === "anime" && form.kind !== initial.kind && form.anilist_id !== "") {
    changes.anilist_id = Number(form.anilist_id);
  }
  if (form.metadata_source !== initial.metadata_source) {
    changes.metadata_source = form.metadata_source
      ? (form.metadata_source as "cinemeta" | "tvdb" | "anilist")
      : null;
  }
  return changes;
}

function toEpisodeMap(form: FormState) {
  return {
    season_offsets: form.offsets.map<SeasonOffset>((offset) => ({
      provider_season: Number(offset.provider_season),
      library_season: Number(offset.library_season),
    })),
    episodes: form.episodes.map<Omit<EpisodeOverride, "id" | "created_at">>((episode) => ({
      season: Number(episode.season),
      episode: Number(episode.episode),
      target_season: Number(episode.target_season),
      target_episode: Number(episode.target_episode),
      absolute: episode.absolute === "" ? null : Number(episode.absolute),
    })),
  };
}

function isValidNumber(value: string, min = 0): boolean {
  return value !== "" && Number.isInteger(Number(value)) && Number(value) >= min;
}

function validate(form: FormState): string | null {
  if (!form.title.trim()) return "Title must not be empty.";
  if (form.year !== "" && !isValidNumber(form.year, 1800)) return "Year must be a number.";
  if (form.kind !== "movie" && form.kind !== "series" && !form.anilist_id && !form.tvdb_id) {
    return "Anime needs an AniList id (or a TVDB id).";
  }
  if (form.kind !== "anime" && !form.imdb_id && !form.tvdb_id) {
    return "Movie and TV entries need an IMDb or TVDB id.";
  }
  if (form.imdb_id && !/^tt\d+$/.test(form.imdb_id)) return "IMDb ids look like tt0000000.";
  for (const item of form.offsets) {
    if (!isValidNumber(item.provider_season) || !isValidNumber(item.library_season)) {
      return "Season offset values must be whole numbers.";
    }
  }
  for (const item of form.episodes) {
    if (
      !isValidNumber(item.season) ||
      !isValidNumber(item.episode, 1) ||
      !isValidNumber(item.target_season) ||
      !isValidNumber(item.target_episode, 1)
    ) {
      return "Episode overrides need whole numbers; episode numbers start at 1.";
    }
    if (item.absolute !== "" && !isValidNumber(item.absolute, 1)) {
      return "Absolute numbers start at 1.";
    }
  }
  return null;
}

const FIELD_CLASS =
  "h-10 w-full rounded-control border border-border-subtle bg-canvas px-3 text-[13px] text-fg placeholder:text-fg-comment focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30";
const LABEL_CLASS = "flex flex-col gap-1 text-[12px] text-fg-muted";
const SMALL_INPUT =
  "h-9 w-full rounded-control border border-border-subtle bg-canvas px-2 text-[12px] text-fg focus:border-accent focus:outline-none";

export interface MetadataFixerDialogProps {
  title: TitleDetail;
  onClose: () => void;
}

export function MetadataFixerDialog({ title, onClose }: MetadataFixerDialogProps) {
  const queryClient = useQueryClient();
  const dialogRef = useRef<HTMLDivElement>(null);
  const [confirmDestructive, setConfirmDestructive] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rebuildConfirm, setRebuildConfirm] = useState(false);
  const [seasonResult, setSeasonResult] = useState<string | null>(null);
  const [relatedIds, setRelatedIds] = useState<string[]>([""]);

  const overridesQuery = useQuery<TitleOverrides>({
    queryKey: ["overrides", title.id],
    queryFn: ({ signal }) => getTitleOverrides(title.id, signal),
  });

  const [form, setForm] = useState<FormState>(() => initialState(title, undefined));
  const [initial, setInitial] = useState<FormState>(() => initialState(title, undefined));
  const [hydrated, setHydrated] = useState(false);

  const suggested = title.classification?.suggested_anilist_id ?? null;

  useEffect(() => {
    if (overridesQuery.data && !hydrated) {
      const next = initialState(title, overridesQuery.data);
      if (title.kind !== "anime" && !next.anilist_id && suggested) {
        next.anilist_id = String(suggested);
      }
      setForm(next);
      setInitial(next);
      setHydrated(true);
    }
  }, [hydrated, overridesQuery.data, suggested, title]);

  useEffect(() => {
    const first = dialogRef.current?.querySelector<HTMLElement>(
      "button, input, select, textarea, [tabindex]:not([tabindex='-1'])",
    );
    first?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const nodes = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
        ),
      ).filter((node) => node.offsetParent !== null);
      if (nodes.length === 0) return;
      const firstNode = nodes[0];
      const lastNode = nodes[nodes.length - 1];
      if (!firstNode || !lastNode) return;
      if (event.shiftKey && document.activeElement === firstNode) {
        event.preventDefault();
        lastNode.focus();
      } else if (!event.shiftKey && document.activeElement === lastNode) {
        event.preventDefault();
        firstNode.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const destructive =
    initial.kind !== "movie" &&
    form.kind === "movie" &&
    (title.episode_count > 0 || title.available_episode_count > 0);

  const save = useMutation({
    mutationFn: async () => {
      const changes = identityChanges(initial, form);
      let response = null;
      if (Object.keys(changes).length > 0) {
        response = await patchTitleIdentity(title.id, {
          ...changes,
          force: destructive ? confirmDestructive : undefined,
        });
      }
      const map = toEpisodeMap(form);
      const mapChanged =
        JSON.stringify(map.season_offsets) !== JSON.stringify(initial.offsets.map((item) => ({
          provider_season: Number(item.provider_season),
          library_season: Number(item.library_season),
        }))) ||
        JSON.stringify(map.episodes) !== JSON.stringify(initial.episodes.map((item) => ({
          season: Number(item.season),
          episode: Number(item.episode),
          target_season: Number(item.target_season),
          target_episode: Number(item.target_episode),
          absolute: item.absolute === "" ? null : Number(item.absolute),
        })));
      if (mapChanged) {
        response = await putEpisodeMap(title.id, map);
      }
      return response;
    },
    onSuccess: (response) => {
      void queryClient.invalidateQueries({ queryKey: ["title", title.id] });
      void queryClient.invalidateQueries({ queryKey: ["title-metadata", title.id] });
      void queryClient.invalidateQueries({ queryKey: ["library"] });
      void queryClient.invalidateQueries({ queryKey: ["releases"] });
      void queryClient.invalidateQueries({ queryKey: ["overrides", title.id] });
      const kept = response?.episode_count ?? title.episode_count;
      const refreshError =
        response && "refresh_error" in response
          ? (response.refresh_error as { code: string; message: string } | undefined)
          : undefined;
      setResult(
        refreshError
          ? `Identity updated, but the provider refresh failed: ${refreshError.message}`
          : `Identity updated; ${kept} episodes kept.`,
      );
      setInitial(form);
      setConfirmDestructive(false);
      setError(null);
    },
    onError: (mutationError: Error) => setError(mutationError.message),
  });

  const remove = useMutation({
    mutationFn: (field: EditableField) => deleteTitleOverride(title.id, field),
    onSuccess: (response) => {
      const next = initialState(response, response.overrides);
      setForm(next);
      setInitial(next);
      void queryClient.invalidateQueries({ queryKey: ["title", title.id] });
      void queryClient.invalidateQueries({ queryKey: ["overrides", title.id] });
      setResult(`Removed the ${remove.variables} override.`);
    },
    onError: (mutationError: Error) => setError(mutationError.message),
  });

  const overridden = useMemo(
    () => new Set(Object.keys(overridesQuery.data?.fields ?? {})),
    [overridesQuery.data],
  );

  const rebuild = useMutation({
    mutationFn: (source: "auto" | "imdb" | "tvdb" | "anilist") =>
      rebuildTitleSeasons(title.id, { source, confirm: rebuildConfirm }),
    onSuccess: (response) => {
      void queryClient.invalidateQueries({ queryKey: ["title", title.id] });
      void queryClient.invalidateQueries({ queryKey: ["title-metadata", title.id] });
      void queryClient.invalidateQueries({ queryKey: ["library"] });
      void queryClient.invalidateQueries({ queryKey: ["releases"] });
      const info = response.rebuild;
      setSeasonResult(
        `Rebuilt from ${info.source}; ${info.kept_episodes} episode rows kept` +
          (info.created > 0 ? `, ${info.created} created` : "") +
          ".",
      );
      setRebuildConfirm(false);
      setError(null);
    },
    onError: (mutationError: Error) => {
      if (mutationError instanceof ApiError && mutationError.code === "REBUILD_REQUIRES_CONFIRM") {
        setRebuildConfirm(true);
        setError("Confirm the rebuild: some episodes still have files.");
        return;
      }
      setError(mutationError.message);
    },
  });

  const saveSeasons = useMutation({
    mutationFn: () => {
      const existing = (title.seasons ?? [])
        .filter((season) => season.source === "anilist" && season.anilist_id !== null)
        .map((season) => ({
          library_season: season.library_season,
          anilist_id: season.anilist_id as number,
        }));
      const ids = relatedIds
        .map((value) => Number(value))
        .filter((value) => Number.isInteger(value) && value > 0);
      const seasons = [...existing];
      let next = seasons.length + 1;
      for (const id of ids) {
        seasons.push({ library_season: next, anilist_id: id });
        next += 1;
      }
      return putTitleSeasons(title.id, { seasons });
    },
    onSuccess: (response) => {
      void queryClient.invalidateQueries({ queryKey: ["title", title.id] });
      void queryClient.invalidateQueries({ queryKey: ["title-metadata", title.id] });
      void queryClient.invalidateQueries({ queryKey: ["library"] });
      const info = response.rebuild;
      setSeasonResult(`Season identity saved; ${info.kept_episodes} episodes now listed.`);
      setRelatedIds([""]);
      setError(null);
    },
    onError: (mutationError: Error) => setError(mutationError.message),
  });

  const anilistOnly =
    title.kind === "anime" &&
    (title.seasons ?? []).length > 0 &&
    (title.seasons ?? []).every((season) => season.source === "anilist");

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((previous) => ({ ...previous, [key]: value }));
  }

  function updateOffset(index: number, patch: Partial<OffsetRow>) {
    setForm((previous) => ({
      ...previous,
      offsets: previous.offsets.map((row, rowIndex) =>
        rowIndex === index ? { ...row, ...patch } : row,
      ),
    }));
  }

  function updateEpisode(index: number, patch: Partial<EpisodeRow>) {
    setForm((previous) => ({
      ...previous,
      episodes: previous.episodes.map((row, rowIndex) =>
        rowIndex === index ? { ...row, ...patch } : row,
      ),
    }));
  }

  function submit() {
    const message = validate(form);
    if (message) {
      setError(message);
      return;
    }
    if (destructive && !confirmDestructive) {
      setError("Confirm the destructive kind change first.");
      return;
    }
    setError(null);
    save.mutate();
  }

  if (overridesQuery.isLoading) {
    return (
      <div className="fixed inset-0 z-50 flex items-end justify-center bg-[rgba(12,14,20,0.7)] p-0 sm:items-center sm:p-4">
        <div className="w-full max-w-[720px] rounded-t-panel border border-border-strong bg-panel px-5 py-6 text-[13px] text-fg-muted sm:rounded-panel">
          Loading metadata overrides…
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-[rgba(12,14,20,0.7)] p-0 sm:items-center sm:p-4">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="fix-metadata-title"
        className="flex max-h-[92dvh] w-full max-w-[720px] flex-col overflow-hidden rounded-t-panel border border-border-strong bg-panel sm:rounded-panel"
      >
        <div className="mx-auto mt-2 h-1 w-8 rounded-full bg-border-strong sm:hidden" />
        <header className="flex items-start justify-between gap-4 border-b border-border-subtle px-4 py-3 sm:px-5">
          <div>
            <h2 id="fix-metadata-title" className="text-[18px] font-semibold text-fg">
              Fix metadata
            </h2>
            <p className="text-[12px] text-fg-muted">{title.title}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="min-h-9 rounded-control border border-border-strong px-3 text-[12px] font-semibold text-fg hover:bg-hover"
          >
            Close
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-4 py-4 sm:px-5">
          <section className="flex flex-col gap-3">
            <h3 className="text-[13px] font-semibold uppercase tracking-wide text-fg-muted">
              Identity
            </h3>
            <div className="flex flex-wrap gap-2" role="group" aria-label="Media kind">
              {KINDS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  aria-pressed={form.kind === option.value}
                  onClick={() => update("kind", option.value)}
                  className={[
                    "min-h-9 rounded-control border px-3 text-[12px] font-semibold",
                    form.kind === option.value
                      ? "border-accent bg-accent/15 text-accent"
                      : "border-border-strong text-fg hover:bg-hover",
                  ].join(" ")}
                >
                  {option.label}
                </button>
              ))}
            </div>

            <label className={LABEL_CLASS}>
              Title
              <input
                className={FIELD_CLASS}
                value={form.title}
                onChange={(event) => update("title", event.target.value)}
              />
            </label>

            <div className="grid gap-3 sm:grid-cols-2">
              <label className={LABEL_CLASS}>
                Year
                <input
                  className={FIELD_CLASS}
                  inputMode="numeric"
                  value={form.year}
                  onChange={(event) => update("year", event.target.value)}
                />
              </label>
              <label className={LABEL_CLASS}>
                Metadata source
                <select
                  className={FIELD_CLASS}
                  value={form.metadata_source}
                  onChange={(event) => update("metadata_source", event.target.value)}
                >
                  {SOURCES.map((source) => (
                    <option key={source.value} value={source.value}>
                      {source.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            {(
              [
                ["imdb_id", "IMDb id", form.imdb_id, "tt0000000"],
                ["tvdb_id", "TVDB id", form.tvdb_id, "12345"],
                ["anilist_id", "AniList id", form.anilist_id, "154587"],
              ] as const
            ).map(([field, label, value, placeholder]) => (
              <label key={field} className={LABEL_CLASS}>
                <span className="flex items-center justify-between gap-2">
                  {label}
                  {overridden.has(field) ? (
                    <button
                      type="button"
                      onClick={() => remove.mutate(field)}
                      className="text-[11px] font-semibold text-accent underline"
                    >
                      Remove override
                    </button>
                  ) : overridesQuery.data?.provider_values[field] ? (
                    <span className="text-[11px] text-fg-comment">
                      provider says {String(overridesQuery.data.provider_values[field])}
                    </span>
                  ) : null}
                </span>
                <input
                  className={FIELD_CLASS}
                  value={value}
                  placeholder={placeholder}
                  onChange={(event) => update(field, event.target.value)}
                />
              </label>
            ))}
          </section>

          <section className="mt-5 flex flex-col gap-3">
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-[13px] font-semibold uppercase tracking-wide text-fg-muted">
                Seasons
              </h3>
              <span className="text-[12px] text-fg-comment">
                {title.seasons?.length ?? 0} in the library
              </span>
            </div>

            <div className="rounded-panel border border-border-subtle">
              {(title.seasons ?? []).length === 0 ? (
                <p className="px-3 py-2 text-[12px] text-fg-muted">No season rows recorded yet.</p>
              ) : (
                <ul className="divide-y divide-border-subtle">
                  {(title.seasons ?? []).map((season) => (
                    <li
                      key={season.library_season}
                      className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-[12px]"
                    >
                      <span className="font-semibold text-fg">
                        {season.library_season === 0
                          ? "Specials"
                          : `Season ${String(season.library_season).padStart(2, "0")}`}
                      </span>
                      <span className="text-fg-muted">
                        {season.source}
                        {season.anilist_id ? ` · AniList ${season.anilist_id}` : ""}
                        {` · ${season.episode_count} episodes`}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="flex flex-wrap gap-2">
              {title.imdb_id ? (
                <button
                  type="button"
                  disabled={rebuild.isPending}
                  onClick={() => rebuild.mutate("imdb")}
                  className="min-h-9 rounded-control border border-border-strong px-3 text-[12px] font-semibold text-fg hover:bg-hover disabled:opacity-50"
                >
                  Rebuild from IMDb
                </button>
              ) : null}
              {title.tvdb_id ? (
                <button
                  type="button"
                  disabled={rebuild.isPending}
                  onClick={() => rebuild.mutate("tvdb")}
                  className="min-h-9 rounded-control border border-border-strong px-3 text-[12px] font-semibold text-fg hover:bg-hover disabled:opacity-50"
                >
                  Rebuild from TVDB
                </button>
              ) : null}
              {title.kind === "anime" && title.anilist_id ? (
                <button
                  type="button"
                  disabled={rebuild.isPending}
                  onClick={() => rebuild.mutate("anilist")}
                  className="min-h-9 rounded-control border border-border-strong px-3 text-[12px] font-semibold text-fg hover:bg-hover disabled:opacity-50"
                >
                  Rebuild from AniList
                </button>
              ) : null}
            </div>

            <p className="text-[12px] text-fg-muted">
              Rebuilding renumbers the current {title.episode_count} episode rows (
              {title.available_episode_count} with files). Episode ids, saved releases and
              files on disk are kept.
            </p>
            {rebuildConfirm ? (
              <label className="flex items-center gap-2 text-[12px] text-danger">
                <input
                  type="checkbox"
                  checked={rebuildConfirm}
                  onChange={(event) => setRebuildConfirm(event.target.checked)}
                />
                I understand some episodes still have files; rebuild anyway.
              </label>
            ) : null}

            {anilistOnly ? (
              <div className="rounded-panel border border-border-subtle">
                <div className="flex items-center justify-between border-b border-border-subtle px-3 py-2">
                  <span className="text-[12px] font-semibold text-fg">Add related seasons</span>
                  <button
                    type="button"
                    onClick={() => setRelatedIds([...relatedIds, ""])}
                    className="text-[12px] font-semibold text-accent"
                  >
                    Add another
                  </button>
                </div>
                <div className="flex flex-col gap-2 p-3">
                  {relatedIds.map((value, index) => (
                    <label key={index} className="text-[11px] text-fg-muted">
                      AniList id for season {(title.seasons?.length ?? 0) + index + 1}
                      <input
                        className={SMALL_INPUT}
                        inputMode="numeric"
                        value={value}
                        onChange={(event) =>
                          setRelatedIds(
                            relatedIds.map((item, rowIndex) =>
                              rowIndex === index ? event.target.value : item,
                            ),
                          )
                        }
                      />
                    </label>
                  ))}
                  <button
                    type="button"
                    disabled={saveSeasons.isPending}
                    onClick={() => saveSeasons.mutate()}
                    className="min-h-9 w-fit rounded-control border border-border-strong px-3 text-[12px] font-semibold text-fg hover:bg-hover disabled:opacity-50"
                  >
                    {saveSeasons.isPending ? "Saving…" : "Save seasons"}
                  </button>
                </div>
              </div>
            ) : null}

            {seasonResult ? (
              <p role="status" className="text-[12px] text-success">
                {seasonResult}
              </p>
            ) : null}
          </section>

          <section className="mt-5 flex flex-col gap-3">
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-[13px] font-semibold uppercase tracking-wide text-fg-muted">
                Episodes
              </h3>
              <span className="text-[12px] text-fg-comment">
                {title.episode_count} episodes
              </span>
            </div>

            <div className="rounded-panel border border-border-subtle">
              <div className="flex items-center justify-between border-b border-border-subtle px-3 py-2">
                <span className="text-[12px] font-semibold text-fg">Season offsets</span>
                <button
                  type="button"
                  onClick={() =>
                    update("offsets", [
                      ...form.offsets,
                      { provider_season: "", library_season: "" },
                    ])
                  }
                  className="text-[12px] font-semibold text-accent"
                >
                  Add offset
                </button>
              </div>
              {form.offsets.length === 0 ? (
                <p className="px-3 py-2 text-[12px] text-fg-muted">No season offsets.</p>
              ) : (
                <div className="flex flex-col gap-2 p-3">
                  {form.offsets.map((offset, index) => (
                    <div key={index} className="grid grid-cols-[1fr_1fr_auto] items-end gap-2">
                      <label className="text-[11px] text-fg-muted">
                        Provider season
                        <input
                          className={SMALL_INPUT}
                          inputMode="numeric"
                          value={offset.provider_season}
                          onChange={(event) =>
                            updateOffset(index, { provider_season: event.target.value })
                          }
                        />
                      </label>
                      <label className="text-[11px] text-fg-muted">
                        Library season
                        <input
                          className={SMALL_INPUT}
                          inputMode="numeric"
                          value={offset.library_season}
                          onChange={(event) =>
                            updateOffset(index, { library_season: event.target.value })
                          }
                        />
                      </label>
                      <button
                        type="button"
                        onClick={() =>
                          update(
                            "offsets",
                            form.offsets.filter((_, rowIndex) => rowIndex !== index),
                          )
                        }
                        className="min-h-9 rounded-control border border-border-strong px-2 text-[11px] text-fg hover:bg-hover"
                      >
                        Remove
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="rounded-panel border border-border-subtle">
              <div className="flex items-center justify-between border-b border-border-subtle px-3 py-2">
                <span className="text-[12px] font-semibold text-fg">Episode overrides</span>
                <button
                  type="button"
                  onClick={() =>
                    update("episodes", [
                      ...form.episodes,
                      {
                        season: "",
                        episode: "",
                        target_season: "",
                        target_episode: "",
                        absolute: "",
                      },
                    ])
                  }
                  className="text-[12px] font-semibold text-accent"
                >
                  Add episode override
                </button>
              </div>
              {form.episodes.length === 0 ? (
                <p className="px-3 py-2 text-[12px] text-fg-muted">No episode overrides.</p>
              ) : (
                <div className="flex flex-col gap-2 p-3">
                  {form.episodes.map((episode, index) => (
                    <div
                      key={index}
                      className="grid grid-cols-2 gap-2 sm:grid-cols-[repeat(5,minmax(0,1fr))_auto]"
                    >
                      {(
                        [
                          ["season", "Season"],
                          ["episode", "Episode"],
                          ["target_season", "New season"],
                          ["target_episode", "New episode"],
                          ["absolute", "Absolute"],
                        ] as const
                      ).map(([field, label]) => (
                        <label key={field} className="text-[11px] text-fg-muted">
                          {label}
                          <input
                            className={SMALL_INPUT}
                            inputMode="numeric"
                            value={episode[field]}
                            onChange={(event) => updateEpisode(index, { [field]: event.target.value })}
                          />
                        </label>
                      ))}
                      <button
                        type="button"
                        onClick={() =>
                          update(
                            "episodes",
                            form.episodes.filter((_, rowIndex) => rowIndex !== index),
                          )
                        }
                        className="min-h-9 self-end rounded-control border border-border-strong px-2 text-[11px] text-fg hover:bg-hover"
                      >
                        Remove
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>

          {destructive ? (
            <section className="mt-5 rounded-panel border-l-2 border-danger bg-panel px-3 py-2">
              <p className="text-[13px] font-semibold text-danger">Destructive change</p>
              <p className="mt-1 text-[12px] text-fg-muted">
                Changing this title to a movie removes its episode rows
                ({title.episode_count} episodes, {title.available_episode_count} with files).
                Files on disk are kept.
              </p>
              <label className="mt-2 flex items-center gap-2 text-[12px] text-fg">
                <input
                  type="checkbox"
                  checked={confirmDestructive}
                  onChange={(event) => setConfirmDestructive(event.target.checked)}
                />
                I understand this removes the episode list.
              </label>
              <p className="mt-2 text-[12px] text-fg-muted">
                Safer alternative:{" "}
                <Link
                  to={`/search?q=${encodeURIComponent(title.title)}`}
                  onClick={onClose}
                  className="font-semibold underline"
                >
                  add a copy from search
                </Link>
                , leaving this entry and its files untouched.
              </p>
            </section>
          ) : null}

          {error ? (
            <p role="alert" className="mt-4 text-[13px] text-danger">
              {error}
            </p>
          ) : null}
          {result ? (
            <p role="status" className="mt-4 text-[13px] text-success">
              {result}
            </p>
          ) : null}
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-border-subtle px-4 py-3 sm:px-5">
          <button
            type="button"
            onClick={onClose}
            className="min-h-10 rounded-control border border-border-strong px-3 text-[13px] font-semibold text-fg hover:bg-hover"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={save.isPending || overridesQuery.isLoading}
            onClick={submit}
            className="min-h-10 rounded-control bg-accent px-4 text-[13px] font-semibold text-canvas hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-60"
          >
            {save.isPending ? "Saving…" : "Save changes"}
          </button>
        </footer>
      </div>
    </div>
  );
}
