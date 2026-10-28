import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { ReleasesSection } from "../components/ReleasesSection";
import { MetadataFixerDialog } from "../components/MetadataFixerDialog";
import { TitleView } from "../components/TitleView";
import { selectionFromParams } from "../lib/episodes";
import {
  deleteTitle,
  getTitle,
  getTitleMetadata,
  patchTitle,
  type Episode,
  type TitleDetail,
  type TitleMetadata,
} from "../lib/api";

function TitleSkeleton() {
  return (
    <div className="grid gap-6 sm:grid-cols-[180px_minmax(0,1fr)]" aria-hidden>
      <div className="aspect-[2/3] animate-pulse rounded-panel bg-hover" />
      <div className="flex flex-col gap-3">
        <div className="h-8 w-2/3 animate-pulse rounded bg-hover" />
        <div className="h-4 w-1/3 animate-pulse rounded bg-hover" />
        <div className="h-20 w-full animate-pulse rounded bg-hover" />
      </div>
    </div>
  );
}

export function TitlePage() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [confirmingDelete, setConfirmingDelete] = useState(false);
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

  const wantedMutation = useMutation({
    mutationFn: (wanted: boolean) => patchTitle(id, { wanted }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["title", id], updated);
      void queryClient.invalidateQueries({ queryKey: ["library"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteTitle(id, true),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["library"] });
      navigate("/library");
    },
  });

  if (error) {
    return (
      <section className="rounded-panel border-l-2 border-danger bg-panel px-4 py-3">
        <p className="text-small text-danger">{error.message}</p>
        <button
          type="button"
          onClick={() => void refetch()}
          className="mt-2 min-h-9 rounded-control border border-border-strong px-3 text-small font-semibold text-fg hover:bg-hover"
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

  const actions = (
    <>
      <button
        type="button"
        aria-pressed={title.wanted}
        disabled={wantedMutation.isPending}
        onClick={() => wantedMutation.mutate(!title.wanted)}
        className={[
          "min-h-11 rounded-control border px-3 text-small font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40",
          title.wanted
            ? "border-accent/50 bg-accent/15 text-accent hover:bg-accent/20"
            : "border-border-strong text-fg hover:bg-hover",
        ].join(" ")}
      >
        {title.wanted ? "Wanted" : "Mark wanted"}
      </button>

      <button
        type="button"
        onClick={() => setFixerOpen(true)}
        className="min-h-11 rounded-control border border-border-strong px-3 text-small font-semibold text-fg hover:bg-hover"
      >
        Fix metadata
      </button>

      {confirmingDelete ? (
        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={deleteMutation.isPending}
            onClick={() => deleteMutation.mutate()}
            className="min-h-11 rounded-control border border-danger bg-danger px-3 text-small font-semibold text-canvas disabled:opacity-40"
          >
            {deleteMutation.isPending ? "Removing…" : "Confirm remove"}
          </button>
          <button
            type="button"
            onClick={() => setConfirmingDelete(false)}
            className="min-h-11 rounded-control border border-border-strong px-3 text-small font-semibold text-fg hover:bg-hover"
          >
            Cancel
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setConfirmingDelete(true)}
          className="min-h-11 rounded-control border border-danger px-3 text-small font-semibold text-danger hover:bg-danger/10"
        >
          Remove
        </button>
      )}
      <span className="text-small text-fg-comment">Removing keeps files on disk.</span>
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
      {wantedMutation.error ? (
        <p role="alert" className="text-small text-danger">
          {wantedMutation.error.message}
        </p>
      ) : null}
      {deleteMutation.error ? (
        <p role="alert" className="text-small text-danger">
          {deleteMutation.error.message}
        </p>
      ) : null}
    </>
  );

  return (
    <>
      <TitleView
      title={title}
      metadata={metadata ?? null}
      back={{ to: "/library", label: "← Library" }}
      actions={actions}
      notices={notices}
      selected={selected}
      onSelectEpisode={toggleEpisode}
      onCollapse={clearSelection}
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
