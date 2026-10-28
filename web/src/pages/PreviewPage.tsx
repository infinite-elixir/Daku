import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import { TitleView } from "../components/TitleView";
import {
  addTitle,
  ApiError,
  getPreview,
  type AddTitleBody,
  type Kind,
  type PreviewTitle,
  type SearchResult,
} from "../lib/api";

const KINDS: Kind[] = ["movie", "series", "anime"];

function metadataSource(source: string): AddTitleBody["metadata_source"] {
  const normalised = source.toLowerCase();
  if (normalised === "anilist" || normalised === "tvdb" || normalised === "cinemeta") {
    return normalised;
  }
  if (normalised === "imdb") {
    return "cinemeta";
  }
  return null;
}

function fallbackPreview(
  kind: Kind,
  source: string,
  sourceId: string,
  candidate: SearchResult | undefined,
): PreviewTitle {
  return {
    id: null,
    source,
    source_id: sourceId,
    library_title_id: null,
    stale: true,
    kind,
    title: candidate?.title ?? sourceId,
    year: candidate?.year ?? null,
    imdb_id: kind === "anime" ? null : sourceId,
    tmdb_id: null,
    tvdb_id: null,
    anilist_id: kind === "anime" ? Number(sourceId) || null : null,
    mal_id: candidate?.id_mal ?? null,
    overview: null,
    poster_url: candidate?.poster_url ?? null,
    banner_url: null,
    folder_name: null,
    wanted: false,
    availability: "none",
    saved_release_count: 0,
    episode_count: 0,
    available_episode_count: 0,
    episodes: [],
  };
}

export function PreviewPage() {
  const { kind = "", source = "", sourceId = "" } = useParams<{
    kind: string;
    source: string;
    sourceId: string;
  }>();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const candidate = (location.state as { candidate?: SearchResult } | null)?.candidate;
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const validKind = KINDS.includes(kind as Kind) ? (kind as Kind) : null;

  const preview = useQuery<PreviewTitle>({
    queryKey: ["preview", source, sourceId, kind],
    queryFn: ({ signal }) =>
      getPreview({ source, source_id: sourceId, kind: kind as Kind }, signal),
    enabled: validKind !== null && source.length > 0 && sourceId.length > 0,
    staleTime: 60_000,
    retry: false,
  });

  const add = useMutation({
    mutationFn: (body: AddTitleBody) => addTitle(body),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: ["library"] });
      navigate(`/title/${created.id}`);
    },
  });

  if (!validKind) {
    return (
      <section className="rounded-panel border-l-2 border-danger bg-panel px-4 py-3">
        <p className="text-small text-danger">Unknown kind: {kind}</p>
        <Link to="/search" className="mt-2 inline-flex min-h-9 items-center text-small font-semibold text-accent">
          Back to search
        </Link>
      </section>
    );
  }

  const outage =
    preview.error instanceof ApiError && preview.error.code === "METADATA_UNAVAILABLE";

  if (preview.error && !outage) {
    return (
      <section className="rounded-panel border-l-2 border-danger bg-panel px-4 py-3">
        <p className="text-small text-danger">{preview.error.message}</p>
        <button
          type="button"
          onClick={() => void preview.refetch()}
          className="mt-2 min-h-11 rounded-control border border-border-strong px-3 text-small font-semibold text-fg hover:bg-hover"
        >
          Retry
        </button>
      </section>
    );
  }

  const data =
    preview.data ?? (outage ? fallbackPreview(validKind, source, sourceId, candidate) : null);

  if (!data) {
    return (
      <section className="flex flex-col gap-4">
        <div className="h-40 animate-pulse rounded-panel bg-hover" aria-hidden />
        <div className="h-8 w-1/2 animate-pulse rounded bg-hover" aria-hidden />
      </section>
    );
  }

  const libraryId = data.library_title_id;
  const selected =
    selectedKey === null
      ? null
      : (data.episodes.find(
          (episode) => `${episode.season}:${episode.episode}` === selectedKey,
        ) ?? null);

  const actions =
    libraryId !== null ? (
      <Link
        to={`/title/${libraryId}`}
        className="inline-flex min-h-11 items-center rounded-control bg-accent px-4 text-small font-semibold text-canvas hover:bg-accent-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        Open
      </Link>
    ) : (
      <button
        type="button"
        disabled={add.isPending}
        onClick={() =>
          add.mutate({
            source: data.source,
            source_id: data.source_id,
            kind: data.kind,
            title: data.title,
            year: data.year,
            poster_url: data.poster_url,
            banner_url: data.banner_url,
            metadata_source: metadataSource(data.source),
            anime: data.anime,
            anilist_id: data.anilist_id,
          })
        }
        className="inline-flex min-h-11 items-center rounded-control bg-accent px-4 text-small font-semibold text-canvas hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        {add.isPending ? "Adding…" : data.anime ? "Add as anime" : "Add to library"}
      </button>
    );

  const notices = (
    <>
      {outage ? (
        <p role="status" className="rounded-panel border-l-2 border-warning bg-panel px-3 py-2 text-small text-warning">
          Metadata is unreachable right now. You can still add this title; its episode list can be
          repaired later.
        </p>
      ) : data.stale ? (
        <p role="status" className="text-small text-warning">
          Showing cached metadata.
        </p>
      ) : null}
      {add.error ? (
        <p role="alert" className="text-small text-danger">
          Could not add the title. {add.error.message}
        </p>
      ) : null}
      {data.anime && libraryId !== null && data.kind === "series" ? (
        <p role="status" className="rounded-panel border-l-2 border-warning bg-panel px-3 py-2 text-small text-warning">
          This looks like anime.{" "}
          <Link to={`/title/${libraryId}?fix=classification`} className="font-semibold underline">
            Fix the classification?
          </Link>
        </p>
      ) : null}
    </>
  );

  return (
    <TitleView
      title={data}
      metadata={data}
      back={{ label: "← Search", onClick: () => navigate(-1) }}
      actions={
        <>
          {actions}
          <span className="text-small text-fg-comment">
            Preview is metadata only. Nothing is downloaded.
          </span>
        </>
      }
      notices={notices}
      selected={selected}
      onSelectEpisode={(episode) => setSelectedKey(`${episode.season}:${episode.episode}`)}
      releases={null}
    />
  );
}
