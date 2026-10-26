import type { Episode } from "./api";

/**
 * Season 0 (specials) always sorts last so it is never the default tab or the
 * episode auto-selected by a title page.
 */
export function seasonNumbers(episodes: Episode[], extra: number[] = []): number[] {
  const values = [...episodes.map((episode) => episode.season), ...extra];
  return [...new Set(values)].sort((left, right) => {
    if (left === 0) {
      return 1;
    }
    if (right === 0) {
      return -1;
    }
    return left - right;
  });
}

export function episodesForSeason(episodes: Episode[], season: number | null): Episode[] {
  if (season === null) {
    return [];
  }
  return episodes
    .filter((episode) => episode.season === season)
    .sort((left, right) => left.episode - right.episode);
}

export function defaultEpisode(episodes: Episode[]): Episode | null {
  const ordered = episodes
    .filter((episode) => episode.season !== 0)
    .sort((left, right) => left.season - right.season || left.episode - right.episode);
  return ordered[0] ?? null;
}

export function findEpisode(
  episodes: Episode[],
  season: number | null,
  episode: number | null,
): Episode | null {
  if (season === null || episode === null) {
    return null;
  }
  return (
    episodes.find((item) => item.season === season && item.episode === episode) ?? null
  );
}

/**
 * Resolve `?season=&episode=` against what the title actually has. An invalid
 * pair falls back to the first non-special episode instead of breaking the page.
 */
export function resolveSelection(
  episodes: Episode[],
  seasonParam: string | null,
  episodeParam: string | null,
): Episode | null {
  const season = seasonParam === null || seasonParam === "" ? null : Number(seasonParam);
  const episode = episodeParam === null || episodeParam === "" ? null : Number(episodeParam);
  if (
    season !== null &&
    episode !== null &&
    Number.isInteger(season) &&
    Number.isInteger(episode)
  ) {
    const match = findEpisode(episodes, season, episode);
    if (match) {
      return match;
    }
  }
  return defaultEpisode(episodes);
}

/**
 * Title pages start fully collapsed. Only a `?season=&episode=` deep link opens a
 * card; an invalid pair falls back to the first non-special episode and opens it.
 */
export function selectionFromParams(
  episodes: Episode[],
  seasonParam: string | null,
  episodeParam: string | null,
): Episode | null {
  const hasParams =
    (seasonParam !== null && seasonParam !== "") || (episodeParam !== null && episodeParam !== "");
  if (!hasParams) {
    return null;
  }
  return resolveSelection(episodes, seasonParam, episodeParam);
}

export function seasonLabel(season: number): string {
  return season === 0 ? "Specials" : `Season ${String(season).padStart(2, "0")}`;
}
