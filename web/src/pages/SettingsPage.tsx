import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { getSettings, patchSettings, type Kind, type ReleaseSettings } from "../lib/api";
import { RELEASE_SOURCES, releaseSourceNote } from "../lib/releaseSources";

const KINDS: Array<{ key: Kind; label: string }> = [
  { key: "movie", label: "Movies" },
  { key: "series", label: "TV" },
  { key: "anime", label: "Anime" },
];
const CATALOGUE = ["yts", "eztv", "1337x", "thepiratebay", "torrentgalaxy"];
const QUALITIES = ["", "480p", "576p", "720p", "1080p", "1440p", "2160p"];
const SORTS = ["seeders", "quality", "size"] as const;

export function SettingsPage() {
  const queryClient = useQueryClient();
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: ({ signal }) => getSettings(signal),
  });
  const [providers, setProviders] = useState<Partial<Record<Kind, string[]>>>({});
  const [minQuality, setMinQuality] = useState<string | null>(null);
  const [sort, setSort] = useState<ReleaseSettings["release"]["sort"] | null>(null);
  const [limit, setLimit] = useState<number | null>(null);
  const [sources, setSources] = useState<Partial<Record<Kind, string[]>>>({});
  const [custom, setCustom] = useState<Partial<Record<Kind, string>>>({});
  const [mpvEnabled, setMpvEnabled] = useState<boolean | null>(null);

  const current = settings.data?.release;
  const player = settings.data?.player;
  const selected = (kind: Kind) => providers[kind] ?? current?.providers[kind] ?? [];
  const changed = useMemo(
    () =>
      Object.keys(providers).length > 0 ||
      Object.keys(sources).length > 0 ||
      minQuality !== null ||
      sort !== null ||
      limit !== null ||
      mpvEnabled !== null,
    [providers, sources, minQuality, sort, limit, mpvEnabled],
  );
  const mutation = useMutation({
    mutationFn: () =>
      patchSettings(
        {
          ...(Object.keys(providers).length ? { providers } : {}),
          ...(Object.keys(sources).length ? { sources } : {}),
          ...(minQuality !== null ? { min_quality: minQuality || null } : {}),
          ...(sort !== null ? { sort } : {}),
          ...(limit !== null ? { limit } : {}),
        },
        mpvEnabled !== null ? { mpv_enabled: mpvEnabled } : undefined,
      ),
    onSuccess: (value) => {
      queryClient.setQueryData(["settings"], value);
      void queryClient.invalidateQueries({ queryKey: ["release-options"] });
      setProviders({});
      setSources({});
      setMinQuality(null);
      setSort(null);
      setLimit(null);
      setMpvEnabled(null);
    },
  });

  if (settings.isLoading) return <p className="text-small text-fg-muted">Loading settings…</p>;
  if (settings.error) return <p role="alert" className="text-small text-danger">{settings.error.message}</p>;
  if (!current) return null;

  function toggle(kind: Kind, provider: string) {
    const next = selected(kind).includes(provider)
      ? selected(kind).filter((item) => item !== provider)
      : [...selected(kind), provider];
    setProviders((old) => ({ ...old, [kind]: next }));
  }
  const selectedSources = (kind: Kind) =>
    sources[kind] ?? settings.data?.sources.by_kind?.[kind] ?? settings.data?.sources.anime_sources ?? [];
  function toggleSource(kind: Kind, source: string) {
    const current = selectedSources(kind);
    const next = current.includes(source)
      ? current.filter((item) => item !== source)
      : [...current, source];
    if (next.length > 0) setSources((old) => ({ ...old, [kind]: next }));
  }

  return (
    <section className="flex max-w-3xl flex-col gap-section">
      <header>
        <h1 className="text-h1 font-bold text-fg">Settings</h1>
        <p className="mt-1 text-small text-fg-muted">Release search defaults are shared by every browser.</p>
      </header>
      <section className="rounded-panel border border-border-subtle bg-panel p-panel">
        <h2 className="text-h2 font-semibold tracking-tight text-fg">Default providers</h2>
        <div className="mt-4 flex flex-col gap-4">
          {KINDS.map(({ key, label }) => (
            <fieldset key={key}>
              <legend className="text-small font-semibold text-fg">{label}</legend>
              <div className="mt-2 flex flex-wrap gap-2">
                {[...new Set([...CATALOGUE, ...selected(key)])].map((provider) => (
                  <label key={provider} className="inline-flex min-h-control-sm items-center rounded-control border border-border-strong px-panel-compact py-grid text-small text-fg">
                    <input type="checkbox" className="mr-1" checked={selected(key).includes(provider)} onChange={() => toggle(key, provider)} />
                    {provider}
                  </label>
                ))}
              </div>
              <div className="mt-2 flex gap-2">
                <input
                  aria-label={`${label} provider`}
                  value={custom[key] ?? ""}
                  onChange={(event) => setCustom((old) => ({ ...old, [key]: event.target.value }))}
                  placeholder="Additional provider"
                  className="min-h-control flex-1 rounded-control border border-border-strong bg-canvas px-panel-compact text-small text-fg"
                />
                <button type="button" className="min-h-control-sm rounded-control border border-border-strong px-panel-compact text-small" onClick={() => {
                  const value = (custom[key] ?? "").trim().toLowerCase();
                  if (value && !selected(key).includes(value)) setProviders((old) => ({ ...old, [key]: [...selected(key), value] }));
                  setCustom((old) => ({ ...old, [key]: "" }));
                }}>Add</button>
                <button type="button" className="min-h-control-sm rounded-control border border-border-strong px-panel-compact text-small" onClick={() => setProviders((old) => ({ ...old, [key]: current.config_providers[key] }))}>Reset to config</button>
              </div>
            </fieldset>
          ))}
        </div>
      </section>
      <section className="rounded-panel border border-border-subtle bg-panel p-panel">
        <h2 className="text-h2 font-semibold tracking-tight text-fg">Release sources</h2>
        <p className="mt-1 text-small text-fg-muted">Each kind searches its selected sources. Torrentio needs an IMDb id; keyword sources answer without one.</p>
        <div className="mt-4 flex flex-col gap-4">
          {KINDS.map(({ key, label }) => (
            <fieldset key={key}>
              <legend className="text-small font-semibold text-fg">{label}</legend>
              <div className="mt-2 flex flex-wrap gap-2">
                {RELEASE_SOURCES.map((source, index) => (
                  <label key={source.id} className="inline-flex min-h-control-sm items-center rounded-control border border-border-strong px-panel-compact py-grid text-small text-fg">
                    <input type="checkbox" className="mr-1" checked={selectedSources(key).includes(source.id)} onChange={() => toggleSource(key, source.id)} />
                    {index + 1} · {source.label}
                  </label>
                ))}
              </div>
              <p className="mt-1 text-micro text-fg-muted">
                {RELEASE_SOURCES.map((source) => `${source.label}: ${releaseSourceNote(source.id)}`).join(" ")}
              </p>
            </fieldset>
          ))}
        </div>
      </section>
      <section className="rounded-panel border border-border-subtle bg-panel p-panel">
        <h2 className="text-h2 font-semibold tracking-tight text-fg">Release search defaults</h2>
        <div className="mt-3 flex flex-wrap gap-3 text-small text-fg-muted">
          <label>Minimum quality <select value={minQuality ?? current.min_quality ?? ""} onChange={(e) => setMinQuality(e.target.value)} className="ml-2 min-h-control rounded-control border border-border-strong bg-canvas px-2 text-fg">{QUALITIES.map((value) => <option key={value} value={value}>{value || "Any"}</option>)}</select></label>
          <label>Sort <select value={sort ?? current.sort} onChange={(e) => setSort(e.target.value as ReleaseSettings["release"]["sort"])} className="ml-2 min-h-control rounded-control border border-border-strong bg-canvas px-2 text-fg">{SORTS.map((value) => <option key={value}>{value}</option>)}</select></label>
          <label>Page size <select value={limit ?? current.limit} onChange={(e) => setLimit(Number(e.target.value))} className="ml-2 min-h-control rounded-control border border-border-strong bg-canvas px-2 text-fg">{[20, 50, 100, 200].map((value) => <option key={value}>{value}</option>)}</select></label>
        </div>
      </section>
      <section className="rounded-panel border border-border-subtle bg-panel p-panel">
        <h2 className="text-h2 font-semibold tracking-tight text-fg">Player</h2>
        <p className="mt-1 text-small text-fg-muted">
          Launch a filed file in the host&apos;s own mpv. The toggle is saved for this
          installation; the binary is read from <code>mpv_bin</code> in{" "}
          <code>config.json</code> or <code>DAKU_MPV_BIN</code>.
        </p>
        <div className="mt-3 flex flex-col gap-3 text-small">
          <label className="inline-flex items-center gap-2 text-fg">
            <input
              type="checkbox"
              checked={mpvEnabled ?? player?.mpv_enabled ?? false}
              onChange={(event) => setMpvEnabled(event.target.checked)}
            />
            Launch downloaded files in mpv
          </label>
          <div>
            <span className="text-fg-muted">mpv binary</span>
            <p className="mt-0.5 font-mono text-fg">{player?.mpv_bin ?? "mpv"}</p>
            <p className="mt-0.5 text-micro text-fg-muted">
              {player?.mpv_path ?? "Not found on PATH or as an absolute path"}
            </p>
          </div>
        </div>
      </section>
      <section className="rounded-panel border border-border-subtle bg-panel p-panel">
        <h2 className="text-h2 font-semibold tracking-tight text-fg">Integrations</h2>
        <p className="mt-1 text-small text-fg-muted">Check qBittorrent, Jellyfin and metadata integrations on the <Link className="text-accent underline" to="/">System</Link> page.</p>
      </section>
      {mutation.error ? <p role="alert" className="text-small text-danger">{mutation.error.message}</p> : null}
      <button type="button" disabled={!changed || mutation.isPending} onClick={() => mutation.mutate()} className="min-h-control self-start rounded-control bg-accent px-panel text-small font-semibold text-canvas disabled:opacity-40">{mutation.isPending ? "Saving…" : "Save settings"}</button>
    </section>
  );
}
