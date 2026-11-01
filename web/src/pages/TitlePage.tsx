import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { DeleteTitleDialog } from "../components/DeleteTitleDialog";
import { PlayIcon, PencilIcon, TrashIcon } from "../components/icons";
import { ReleasesSection } from "../components/ReleasesSection";
import { MetadataFixerDialog } from "../components/MetadataFixerDialog";
import { TitleView } from "../components/TitleView";
import { selectionFromParams } from "../lib/episodes";
import {
  deleteTitle,
  getTitle,
  getTitleFiles,
  getTitleMetadata,
  patchEpisode,
  patchTitle,
  playMediaFile,
  reconcileTitleMedia,
  refreshTitleMetadata,
  type Episode,
  type MediaFileItem,
  type TitleDetail,
  type TitleFilesResponse,
  type TitleMetadata,
  type WatchStatus,
} from "../lib/api";
import { WATCH_STATUS_OPTIONS } from "../lib/watch";

function choosePlayableFile(
  files: TitleFilesResponse | null | undefined,
  title: TitleDetail,
): MediaFileItem | null {
  const items = files?.items ?? [];
  if (items.length === 0) {
    return null;
  }
  if (title.kind !== "movie" && title.next_episode) {
    const next = title.next_episode;
    const episode = title.episodes.find(
      (row) => row.season === next.season && row.episode === next.episode,
    );
    if (episode) {
      const match = items.find((file) => file.episode_id === episode.id);
      if (match) {
        return match;
      }
    }
  }
  return items.find((file) => file.is_primary) ?? items[0] ?? null;
}

function TitleSkeleton() {
  return (
    <div className="flex flex-col gap-section" data-testid="title-skeleton" aria-hidden>
      <div className="title-hero-scope">
        <div className="title-hero grid grid-cols-1 gap-gutter border-l-2 border-border-strong pl-3 @min-[720px]:grid-cols-[180px_minmax(0,1fr)] @min-[1024px]:grid-cols-[minmax(160px,220px)_minmax(0,1fr)]">
          <div
            data-testid="title-skeleton-poster"
            className="title-hero-poster aspect-[2/3] animate-pulse rounded-panel bg-hover"
          />
          <div
            data-testid="title-skeleton-identity"
            className="flex min-w-0 flex-col gap-3"
          >
            <div className="h-5 w-40 animate-pulse rounded bg-hover" />
            <div className="h-8 w-2/3 animate-pulse rounded bg-hover" />
            <div className="h-4 w-1/3 animate-pulse rounded bg-hover" />
            <div className="h-6 w-2/5 animate-pulse rounded bg-hover" />
            <div className="h-4 w-1/2 animate-pulse rounded bg-hover" />
            <div className="mt-auto flex flex-wrap gap-2 pt-1">
              <div className="h-control w-28 animate-pulse rounded-control bg-hover" />
              <div className="h-control w-28 animate-pulse rounded-control bg-hover" />
            </div>
          </div>
        </div>
      </div>
      <div className="title-facts-scope">
        <div className="grid grid-cols-1 items-start gap-section @min-[900px]:grid-cols-[minmax(0,68ch)_minmax(200px,1fr)]">
          <div className="flex flex-col gap-grid">
            <div className="h-4 w-full animate-pulse rounded bg-hover" />
            <div className="h-4 w-5/6 animate-pulse rounded bg-hover" />
            <div className="h-4 w-2/3 animate-pulse rounded bg-hover" />
          </div>
          <div
            data-testid="title-skeleton-facts"
            className="title-facts flex flex-col gap-tight"
          >
            <div className="h-4 w-3/4 animate-pulse rounded bg-hover" />
            <div className="h-4 w-2/3 animate-pulse rounded bg-hover" />
            <div className="h-4 w-4/5 animate-pulse rounded bg-hover" />
          </div>
        </div>
      </div>
    </div>
  );
}

export function TitlePage() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [fixerOpen, setFixerOpen] = useState(searchParams.get("fix") === "classification");

  const { data: title, error, isLoading, refetch } = useQuery<TitleDetail>({
    queryKey: ["title", id],
    queryFn: ({ signal }) => getTitle(id, signal),
    enabled: id.length > 0,
    staleTime: 30_000,
  });

  const { data: metadata } = useQuery<TitleMetadata>({
    queryKey: ["title-metadata", id],
    queryFn: ({ signal }) => getTitleMetadata(id, signal),
    enabled: id.length > 0,
    staleTime: 10 * 60_000,
    retry: false,
  });

  const { data: files } = useQuery<TitleFilesResponse>({
    queryKey: ["title-files", id],
    queryFn: ({ signal }) => getTitleFiles(id, signal),
    enabled: id.length > 0,
    staleTime: 30_000,
    retry: false,
  });

  const watchStatusMutation = useMutation({
    mutationFn: ({
      status,
      clearProgress,
    }: {
      status: WatchStatus | null;
      clearProgress: boolean;
    }) => patchTitle(id, { watch_status: status, clear_progress: clearProgress }),
    onMutate: async ({ status }) => {
      await queryClient.cancelQueries({ queryKey: ["title", id] });
      const previous = queryClient.getQueryData<TitleDetail>(["title", id]);
      if (previous) {
        queryClient.setQueryData(["title", id], { ...previous, watch_status: status });
      }
      return { previous };
    },
    onError: (_error, _variables, context) => {
      if (context?.previous) {
        queryClient.setQueryData(["title", id], context.previous);
      }
    },
    onSuccess: (updated) => {
      queryClient.setQueryData(["title", id], updated);
      void queryClient.invalidateQueries({ queryKey: ["library"] });
    },
  });

  const episodeMutation = useMutation({
    mutationFn: ({ episodeId, watched }: { episodeId: string; watched: boolean }) =>
      patchEpisode(id, episodeId, watched),
    onMutate: async ({ episodeId, watched }) => {
      await queryClient.cancelQueries({ queryKey: ["title", id] });
      const previous = queryClient.getQueryData<TitleDetail>(["title", id]);
      if (previous) {
        queryClient.setQueryData(["title", id], {
          ...previous,
          episodes: previous.episodes.map((episode) =>
            episode.id === episodeId ? { ...episode, watched } : episode,
          ),
        });
      }
      return { previous };
    },
    onError: (_error, _variables, context) => {
      if (context?.previous) {
        queryClient.setQueryData(["title", id], context.previous);
      }
    },
    onSuccess: (updated) => {
      queryClient.setQueryData(["title", id], updated);
      void queryClient.invalidateQueries({ queryKey: ["library"] });
    },
  });

  const bulkWatchMutation = useMutation({
    mutationFn: async (episodes: Episode[]) => {
      let updated: TitleDetail | null = null;
      for (const episode of episodes) {
        updated = await patchEpisode(id, episode.id, true);
      }
      return updated;
    },
    onSuccess: (updated) => {
      if (updated) {
        queryClient.setQueryData(["title", id], updated);
      }
      void queryClient.invalidateQueries({ queryKey: ["library"] });
    },
  });

  function changeWatchStatus(status: WatchStatus | null) {
    const leavingCompleted = title?.watch_status === "completed" && status !== "completed";
    const clearProgress =
      leavingCompleted &&
      window.confirm("Clear watch progress for every episode? Cancel keeps your progress.");
    watchStatusMutation.mutate({ status, clearProgress });
  }

  const playMutation = useMutation({
    mutationFn: () => {
      if (!title) {
        throw new Error("Title is not loaded");
      }
      const file = choosePlayableFile(files, title);
      if (!file) {
        throw new Error("No downloaded file to play");
      }
      return playMediaFile(file.id);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (deleteFiles: boolean) => deleteTitle(id, !deleteFiles),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["library"] });
      navigate("/library");
    },
  });

  const cleanupMutation = useMutation({
    mutationFn: () => reconcileTitleMedia(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["title", id] });
      void queryClient.invalidateQueries({ queryKey: ["title-files", id] });
      void queryClient.invalidateQueries({ queryKey: ["library"] });
    },
  });

  const refreshMetadataMutation = useMutation({
    mutationFn: () => refreshTitleMetadata(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["title", id] });
      void queryClient.invalidateQueries({ queryKey: ["title-files", id] });
      void queryClient.invalidateQueries({ queryKey: ["title-metadata", id] });
      void queryClient.invalidateQueries({ queryKey: ["library"] });
    },
  });

  if (error) {
    return (
      <section className="rounded-panel border-l-2 border-danger bg-panel px-panel py-panel-compact">
        <p className="text-small text-danger">{error.message}</p>
        <button
          type="button"
          onClick={() => void refetch()}
          className="mt-2 min-h-control-sm rounded-control border border-border-strong px-3 text-small font-semibold text-fg hover:bg-hover"
        >
          Retry
        </button>
      </section>
    );
  }

  if (isLoading || !title) {
    return <TitleSkeleton />;
  }

  const selected = selectionFromParams(
    title.episodes,
    searchParams.get("season"),
    searchParams.get("episode"),
  );

  function clearSelection() {
    const next = new URLSearchParams(searchParams);
    next.delete("season");
    next.delete("episode");
    setSearchParams(next);
  }

  function toggleEpisode(episode: Episode) {
    const next = new URLSearchParams(searchParams);
    if (selected?.id === episode.id) {
      next.delete("season");
      next.delete("episode");
      setSearchParams(next);
      return;
    }
    next.set("season", String(episode.season));
    next.set("episode", String(episode.episode));
    setSearchParams(next);
    window.setTimeout(() => {
      document
        .getElementById(`episode-card-${episode.season}-${episode.episode}`)
        ?.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
    }, 0);
  }

  const playable = choosePlayableFile(files, title);

  const actions = (
    <>
      <button
        type="button"
        aria-label="Play"
        title={playable ? "Play in mpv" : "Nothing downloaded yet"}
        disabled={!playable || playMutation.isPending}
        onClick={() => playMutation.mutate()}
        className="inline-flex min-h-control items-center gap-2 rounded-control border border-border-strong px-3 text-small font-semibold text-fg transition-colors hover:bg-hover disabled:cursor-not-allowed disabled:border-border-subtle disabled:text-fg-comment disabled:opacity-50"
      >
        <PlayIcon className="h-3.5 w-3.5" />
        <span>{playMutation.isPending ? "Playing…" : "Play"}</span>
      </button>

      <label className="inline-flex items-center">
        <select
          aria-label="Watch status"
          value={title.watch_status ?? ""}
          disabled={watchStatusMutation.isPending}
          onChange={(event) =>
            changeWatchStatus(
              event.target.value === "" ? null : (event.target.value as WatchStatus),
            )
          }
          className="min-h-control rounded-control border border-border-strong bg-panel px-2 text-small font-semibold text-fg disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {WATCH_STATUS_OPTIONS.map((option) => (
            <option key={option.value || "untracked"} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>

      <button
        type="button"
        aria-label="Fix metadata"
        title="Fix metadata"
        onClick={() => setFixerOpen(true)}
        className="inline-flex min-h-control w-control items-center justify-center rounded-control border border-border-strong text-fg hover:bg-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        <PencilIcon />
      </button>

      <button
        type="button"
        aria-label="Delete title"
        title="Delete title"
        onClick={() => setDeleteOpen(true)}
        className="inline-flex min-h-control w-control items-center justify-center rounded-control border border-danger/60 text-danger hover:bg-danger/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        <TrashIcon />
      </button>
    </>
  );

  const notices = (
    <>
      {title.classification?.is_anime && title.kind !== "anime" ? (
        <p
          role="status"
          className="rounded-panel border-l-2 border-warning bg-panel px-3 py-2 text-small text-warning"
        >
          This looks like anime.{" "}
          <button
            type="button"
            onClick={() => setFixerOpen(true)}
            className="font-semibold underline"
          >
            Fix the classification?
          </button>
        </p>
      ) : null}
      {!title.poster_url ? (
        <p
          role="status"
          className="rounded-panel border-l-2 border-warning bg-panel px-3 py-2 text-small text-warning"
        >
          Artwork unavailable.{" "}
          <button
            type="button"
            disabled={refreshMetadataMutation.isPending}
            onClick={() => refreshMetadataMutation.mutate()}
            className="font-semibold underline disabled:opacity-50"
          >
            {refreshMetadataMutation.isPending ? "Refreshing…" : "Refresh metadata"}
          </button>
          {refreshMetadataMutation.data?.refresh_error ? (
            <span className="block text-fg-muted">
              {refreshMetadataMutation.data.refresh_error.message}
            </span>
          ) : null}
        </p>
      ) : null}
      {refreshMetadataMutation.error ? (
        <p role="alert" className="text-small text-danger">
          {refreshMetadataMutation.error.message}
        </p>
      ) : null}
      {playMutation.error ? (
        <p role="alert" className="text-small text-danger">
          {playMutation.error.message}
        </p>
      ) : null}
      {watchStatusMutation.error ? (
        <p role="alert" className="text-small text-danger">
          {watchStatusMutation.error.message}
        </p>
      ) : null}
      {episodeMutation.error ? (
        <p role="alert" className="text-small text-danger">
          {episodeMutation.error.message}
        </p>
      ) : null}
      {bulkWatchMutation.error ? (
        <p role="alert" className="text-small text-danger">
          {bulkWatchMutation.error.message}
        </p>
      ) : null}
      {deleteMutation.error ? (
        <p role="alert" className="text-small text-danger">
          {deleteMutation.error.message}
        </p>
      ) : null}
      {cleanupMutation.error ? (
        <p role="alert" className="text-small text-danger">
          {cleanupMutation.error.message}
        </p>
      ) : null}
    </>
  );

  return (
    <>
      <TitleView
      title={title}
      metadata={metadata ?? null}
      files={files ?? null}
      back={{ to: "/library", label: "← Library" }}
      actions={actions}
      notices={notices}
      emptyEpisodesAction={
        title.poster_url ? (
          <button
            type="button"
            disabled={refreshMetadataMutation.isPending}
            onClick={() => refreshMetadataMutation.mutate()}
            className="min-h-control rounded-control border border-border-strong px-3 text-small font-semibold text-fg hover:bg-hover disabled:cursor-not-allowed disabled:opacity-60"
          >
            {refreshMetadataMutation.isPending ? "Refreshing…" : "Refresh metadata"}
          </button>
        ) : undefined
      }
      selected={selected}
      onSelectEpisode={toggleEpisode}
      onToggleWatched={(episode, watched) =>
        episodeMutation.mutate({ episodeId: episode.id, watched })
      }
      onBulkWatched={(episodes) => bulkWatchMutation.mutate(episodes)}
      bulkWatchPending={bulkWatchMutation.isPending}
      onCollapse={clearSelection}
      onCleanupMissing={() => cleanupMutation.mutate()}
      cleanupPending={cleanupMutation.isPending}
      renderEpisodeExpanded={
        title.kind === "movie"
          ? undefined
          : (episode) => (
              <ReleasesSection
                title={title}
                mode="find"
                season={episode.season}
                episode={episode.episode}
              />
            )
      }
      releases={
        <div id="releases" className="scroll-mt-6">
          {title.kind === "movie" ? (
            <ReleasesSection title={title} mode="all" />
          ) : (
            <ReleasesSection
              title={title}
              mode="saved"
              season={selected?.season ?? null}
              episode={selected?.episode ?? null}
            />
          )}
        </div>
      }
    />
      {deleteOpen ? (
        <DeleteTitleDialog
          titleName={title.title}
          pending={deleteMutation.isPending}
          error={deleteMutation.error?.message ?? null}
          onCancel={() => setDeleteOpen(false)}
          onConfirm={(deleteFiles) => deleteMutation.mutate(deleteFiles)}
        />
      ) : null}
      {fixerOpen ? (
        <MetadataFixerDialog
          title={title}
          onClose={() => {
            setFixerOpen(false);
            if (searchParams.get("fix") === "classification") {
              const next = new URLSearchParams(searchParams);
              next.delete("fix");
              setSearchParams(next, { replace: true });
            }
          }}
        />
      ) : null}
    </>
  );
}
