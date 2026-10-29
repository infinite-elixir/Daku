import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { seasonLabel, episodesForSeason, seasonNumbers } from "../lib/episodes";
import type { Availability, Episode, Kind, SeasonInfo, TitleMetadata } from "../lib/api";
import { AvailabilityPill, EpisodeStatusPill, KindBadge } from "./Pills";
import { ExpandableText } from "./ExpandableText";
import { MetadataFacts } from "./MetadataFacts";
import { PosterArt } from "./PosterArt";
import { RatingsRow } from "./RatingsRow";

export interface TitleViewData {
  kind: Kind;
  title: string;
  year: number | null;
  imdb_id: string | null;
  anilist_id: number | null;
  overview: string | null;
  poster_url: string | null;
  banner_url: string | null;
  folder_name: string | null;
  availability: Availability;
  wanted: boolean;
  saved_release_count: number;
  episode_count: number;
  available_episode_count: number;
  episodes: Episode[];
  seasons?: SeasonInfo[];
}

export interface TitleViewProps {
  title: TitleViewData;
  /** Display-only ratings and credits. Omitted entirely for a metadata-only outage. */
  metadata?: TitleMetadata | null;
  back: { label: string; to?: string; onClick?: () => void };
  actions?: ReactNode;
  /** Release UI for a library title. `null` renders a metadata-only preview. */
  releases?: ReactNode;
  selected?: Episode | null;
  onSelectEpisode?: (episode: Episode) => void;
  /** Called when switching seasons should close the currently open card. */
  onCollapse?: () => void;
  /** Rendered inside the expanded episode card. Omit for a read-only preview. */
  renderEpisodeExpanded?: (episode: Episode) => ReactNode;
  emptyEpisodesAction?: ReactNode;
  notices?: ReactNode;
}

const TAB_CLASS =
  "min-h-11 shrink-0 rounded-control border px-4 text-small font-semibold transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";
const ROW_CLASS =
  "flex min-h-11 w-full items-center gap-3 px-4 py-2 text-left transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-accent";

/** Direct banner artwork, with a gradient fallback when there is no wide art. */
function BannerArt({ url }: { url: string | null }) {
  const [failed, setFailed] = useState(false);

  if (!url || failed) {
    return <div data-testid="title-banner-gradient" className="banner-fallback h-full w-full" />;
  }

  return (
    <img
      data-testid="title-banner-image"
      src={url}
      alt=""
      draggable={false}
      decoding="async"
      onError={() => setFailed(true)}
      className="h-full w-full object-cover object-center"
    />
  );
}

export function TitleView({
  title,
  metadata = null,
  back,
  actions,
  releases,
  selected = null,
  onSelectEpisode,
  onCollapse,
  renderEpisodeExpanded,
  emptyEpisodesAction,
  notices,
}: TitleViewProps) {
  const seasons = useMemo(
    () =>
      seasonNumbers(
        title.episodes,
        (title.seasons ?? []).map((season) => season.library_season),
      ),
    [title.episodes, title.seasons],
  );
  const [activeSeason, setActiveSeason] = useState<number | null>(
    () => selected?.season ?? seasons[0] ?? null,
  );
  const visibleEpisodes = useMemo(
    () => episodesForSeason(title.episodes, activeSeason),
    [title.episodes, activeSeason],
  );
  const tabRefs = useRef(new Map<number, HTMLButtonElement>());
  const isMovie = title.kind === "movie";
  // Prefer real wide art so the banner is not a hard crop of a 2:3 poster.
  const bannerUrl = title.banner_url ?? metadata?.banner_url ?? title.poster_url;

  // A deep link (or a click) selects an episode; follow it to the right tab.
  useEffect(() => {
    if (selected !== null) {
      setActiveSeason(selected.season);
    }
  }, [selected]);

  // Metadata refresh can add or remove seasons under us.
  useEffect(() => {
    if (activeSeason !== null && !seasons.includes(activeSeason)) {
      setActiveSeason(seasons[0] ?? null);
    }
  }, [seasons, activeSeason]);

  function changeSeason(season: number) {
    setActiveSeason(season);
    if (selected !== null && selected.season !== season) {
      onCollapse?.();
    }
  }

  function handleTabKeyDown(event: React.KeyboardEvent<HTMLButtonElement>, season: number) {
    if (seasons.length === 0) {
      return;
    }
    const index = seasons.indexOf(season);
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      nextIndex = (index + 1) % seasons.length;
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      nextIndex = (index - 1 + seasons.length) % seasons.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = seasons.length - 1;
    }
    if (nextIndex === null) {
      return;
    }
    event.preventDefault();
    const nextSeason = seasons[nextIndex];
    if (nextSeason === undefined) {
      return;
    }
    tabRefs.current.get(nextSeason)?.focus();
    changeSeason(nextSeason);
  }

  return (
    <section className="flex flex-col gap-8">
      <Link
        to={back.to ?? "/search"}
        onClick={
          back.onClick
            ? (event) => {
                event.preventDefault();
                back.onClick?.();
              }
            : undefined
        }
        className="w-fit text-small font-semibold text-fg-muted hover:text-fg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        {back.label}
      </Link>

      <div className="relative">
        <div
          data-testid="title-banner"
          className="title-banner-strip title-banner-art pointer-events-none absolute inset-x-0 top-0 h-[300px] overflow-hidden rounded-panel"
          aria-hidden
        >
          <BannerArt url={bannerUrl} />
          <div data-testid="title-banner-scrim" className="title-banner-scrim absolute inset-0" />
        </div>

        <div className="relative grid gap-6 sm:grid-cols-[180px_minmax(0,1fr)] md:pt-6">
          <div className="w-40 sm:w-full">
            <PosterArt url={title.poster_url} title={title.title} />
          </div>

        <div className="flex min-w-0 flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <KindBadge kind={title.kind} />
            <AvailabilityPill availability={title.availability} />
            {title.wanted ? (
              <span className="rounded-full bg-accent/15 px-2 py-0.5 text-micro font-medium text-accent">
                Wanted
              </span>
            ) : null}
          </div>

          <div className="border-l-2 border-border-strong pl-3">
            <h1 className="line-clamp-2 break-words text-h1 font-bold text-fg">{title.title}</h1>
            <p className="text-small text-fg-muted">
              {title.year ?? "Year unknown"}
              {title.imdb_id ? ` · ${title.imdb_id}` : ""}
              {title.anilist_id ? ` · AniList ${title.anilist_id}` : ""}
            </p>
          </div>

          {notices}

          <RatingsRow ratings={metadata ?? {}} />

          <ExpandableText text={title.overview} />

          {metadata ? <MetadataFacts facts={metadata} /> : null}

          <dl className="flex flex-wrap gap-x-6 gap-y-2 text-small">
            <div>
              <dt className="text-fg-comment">Saved releases</dt>
              <dd className="font-semibold text-fg">{title.saved_release_count}</dd>
            </div>
            {!isMovie ? (
              <div>
                <dt className="text-fg-comment">Episodes</dt>
                <dd className="font-semibold text-fg">
                  {title.available_episode_count}/{title.episode_count}
                </dd>
              </div>
            ) : null}
            {title.folder_name ? (
              <div>
                <dt className="text-fg-comment">Folder</dt>
                <dd className="break-all font-semibold text-fg">{title.folder_name}</dd>
              </div>
            ) : null}
          </dl>

          {actions ? <div className="flex flex-wrap items-center gap-2 pt-1">{actions}</div> : null}
        </div>
        </div>
      </div>

      {!isMovie ? (
        <section className="flex flex-col gap-4" aria-labelledby="episodes-heading">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 id="episodes-heading" className="text-h2 font-semibold tracking-tight text-fg">
              Episodes
            </h2>
            {renderEpisodeExpanded ? (
              <p className="text-small text-fg-comment">
                Expand an episode to see its releases.
              </p>
            ) : null}
          </div>
          {seasons.length === 0 ? (
            <div className="rounded-panel border border-border-subtle bg-panel px-4 py-6">
              <p className="text-h2 font-semibold tracking-tight text-fg">No episodes recorded yet</p>
              <p className="mt-1 max-w-[60ch] text-small text-fg-muted">
                This title was added without an episode list, usually because metadata was
                unreachable at the time. Refreshing its metadata will fill it in.
              </p>
              <div className="mt-3">
                {emptyEpisodesAction ?? (
                  <button
                    type="button"
                    disabled
                    title="Metadata refresh arrives with T-022"
                    className="min-h-11 rounded-control border border-border-strong px-3 text-small font-semibold text-fg-muted disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    Refresh metadata
                  </button>
                )}
              </div>
            </div>
          ) : (
            <>
              <div
                role="tablist"
                aria-label="Seasons"
                className="flex gap-2 overflow-x-auto whitespace-nowrap pb-1 [scrollbar-width:none]"
              >
                {seasons.map((season) => {
                  const active = season === activeSeason;
                  return (
                    <button
                      key={season}
                      ref={(node) => {
                        if (node) {
                          tabRefs.current.set(season, node);
                        } else {
                          tabRefs.current.delete(season);
                        }
                      }}
                      type="button"
                      role="tab"
                      id={`season-tab-${season}`}
                      aria-selected={active}
                      aria-controls={`season-panel-${season}`}
                      tabIndex={active ? 0 : -1}
                      onClick={() => changeSeason(season)}
                      onKeyDown={(event) => handleTabKeyDown(event, season)}
                      className={[
                        TAB_CLASS,
                        active
                          ? "border-accent/60 bg-accent/15 text-accent"
                          : "border-border-subtle text-fg-muted hover:bg-hover hover:text-fg",
                      ].join(" ")}
                    >
                      {season === 0 ? "Specials" : seasonLabel(season)}
                    </button>
                  );
                })}
              </div>

              <div
                role="tabpanel"
                id={`season-panel-${activeSeason ?? 0}`}
                aria-labelledby={`season-tab-${activeSeason ?? 0}`}
              >
                <ul className="flex flex-col gap-2">
                  {visibleEpisodes.map((episode) => {
                    const expanded = selected?.id === episode.id;
                    const expandable = Boolean(renderEpisodeExpanded);
                    return (
                      <li
                        key={episode.id}
                        id={`episode-card-${episode.season}-${episode.episode}`}
                        className={[
                          "overflow-hidden rounded-panel border bg-panel",
                          expanded ? "border-accent/60" : "border-border-subtle",
                        ].join(" ")}
                      >
                        <button
                          type="button"
                          aria-expanded={expandable ? expanded : undefined}
                          aria-controls={
                            expandable ? `episode-panel-${episode.season}-${episode.episode}` : undefined
                          }
                          onClick={() => onSelectEpisode?.(episode)}
                          className={[
                            ROW_CLASS,
                            expanded ? "bg-accent/10 text-fg" : "hover:bg-hover",
                          ].join(" ")}
                        >
                          {expandable ? (
                            <span
                              aria-hidden
                              className={[
                                "text-micro transition-transform",
                                expanded ? "rotate-90 text-accent" : "text-fg-comment",
                              ].join(" ")}
                            >
                              ▶
                            </span>
                          ) : null}
                          <span className="font-mono text-small tabular-nums text-fg-muted">
                            {episode.label}
                          </span>
                          <span className="min-w-0 flex-1 truncate text-body text-fg">
                            {episode.name ?? `Episode ${episode.episode}`}
                          </span>
                          {title.kind === "anime" && episode.absolute !== null ? (
                            <span className="text-micro text-fg-comment">#{episode.absolute}</span>
                          ) : null}
                          {expanded ? (
                            <span className="inline-flex items-center rounded-full bg-accent/15 px-2 py-0.5 text-micro font-semibold text-accent">
                              ✓ {expandable ? "Open" : "Selected"}
                            </span>
                          ) : null}
                          <EpisodeStatusPill status={episode.status} />
                        </button>
                        {expandable && expanded ? (
                          <div
                            id={`episode-panel-${episode.season}-${episode.episode}`}
                            className="border-t border-border-subtle px-3 py-3"
                          >
                            {renderEpisodeExpanded?.(episode)}
                          </div>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              </div>
            </>
          )}
        </section>
      ) : null}

      {releases}
    </section>
  );
}
