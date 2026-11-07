/**
 * Release-source registry.
 *
 * Numbers are stable UI labels, not API values. Add a new source here and it
 * automatically appears in Settings, the release filter and the search status.
 */
export interface ReleaseSource {
  id: string;
  label: string;
}

export const RELEASE_SOURCES: ReleaseSource[] = [
  { id: "torrentio", label: "Torrentio" },
  { id: "animetosho", label: "AnimeTosho" },
  { id: "torrents-csv", label: "Torrents-CSV" },
];

const SOURCE_NOTES: Record<string, string> = {
  torrentio: "Stremio addon scraping curated public sites; needs an IMDb id.",
  animetosho: "Anime-only JSON feed with query search.",
  "torrents-csv": "DHT-wide index for movies and TV with query search.",
};

export function releaseSourceNote(id: string): string {
  return SOURCE_NOTES[id] ?? "";
}

function prettify(id: string): string {
  return id
    .split(/[-_]/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function releaseSourceLabel(id: string): string {
  return RELEASE_SOURCES.find((source) => source.id === id)?.label ?? prettify(id);
}

export function releaseSourceNumber(id: string): number | null {
  const index = RELEASE_SOURCES.findIndex((source) => source.id === id);
  return index === -1 ? null : index + 1;
}

export interface ReleaseSourceChoice extends ReleaseSource {
  number: number;
}

export function releaseSourceChoices(configured: string[] = []): ReleaseSourceChoice[] {
  const choices = RELEASE_SOURCES.map((source, index) => ({ ...source, number: index + 1 }));
  for (const id of configured) {
    if (choices.some((choice) => choice.id === id)) continue;
    choices.push({ id, label: prettify(id), number: choices.length + 1 });
  }
  return choices;
}

export function describeReleaseSources(sources: string[]): string {
  if (sources.length === 0) return "Searching sources…";
  const names = sources.map((id) => {
    const number = releaseSourceNumber(id);
    return number === null ? releaseSourceLabel(id) : `${number} ${releaseSourceLabel(id)}`;
  });
  return `Searching ${sources.length === 1 ? "source" : "sources"} ${names.join(", ")}…`;
}
