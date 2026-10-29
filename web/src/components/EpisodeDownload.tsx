import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import {
  ApiError,
  previewSelection,
  startDownload,
  type DownloadMatchFile,
  type DownloadSelection,
  type ReleaseItem,
} from "../lib/api";
import { formatBytes } from "../lib/format";

type Phase = "idle" | "loading" | "confirm" | "choose" | "error";

interface Chooser {
  files: DownloadMatchFile[];
  candidates: DownloadMatchFile[];
  label: string;
}

function detailObject(error: unknown): Record<string, unknown> {
  if (!(error instanceof ApiError) || typeof error.detail !== "object" || error.detail === null) {
    return {};
  }
  return error.detail as Record<string, unknown>;
}

function detailFiles(value: unknown): DownloadMatchFile[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter(
    (item): item is DownloadMatchFile =>
      typeof item === "object" &&
      item !== null &&
      typeof (item as DownloadMatchFile).path === "string" &&
      typeof (item as DownloadMatchFile).index === "number",
  );
}

export function EpisodeDownload({
  release,
  episodeId,
  episodeLabel,
  ensureRelease,
  onQueued,
}: {
  release: ReleaseItem;
  episodeId: string;
  episodeLabel: string;
  ensureRelease: () => Promise<string>;
  onQueued: (message: string) => void;
}) {
  const queryClient = useQueryClient();
  const [phase, setPhase] = useState<Phase>("idle");
  const [releaseId, setReleaseId] = useState<string | null>(release.release_id);
  const [preview, setPreview] = useState<DownloadSelection | null>(null);
  const [chooser, setChooser] = useState<Chooser | null>(null);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [packConfirmed, setPackConfirmed] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const files = useMemo(() => {
    if (chooser) {
      return chooser.files;
    }
    return preview?.files ?? [];
  }, [chooser, preview]);

  const confirmed = useMemo(() => {
    if (!preview) {
      return null;
    }
    const match = preview.match ?? {};
    const paths = Array.isArray(match.file_paths)
      ? match.file_paths.filter((item): item is string => typeof item === "string")
      : [];
    const index = typeof match.file_index === "number" ? match.file_index : null;
    const file =
      files.find((item) => paths.includes(item.path)) ??
      (index !== null ? files.find((item) => item.index === index) : undefined);
    const path = paths[0] ?? file?.path ?? release.filename;
    return {
      path: path ?? "the release",
      size: file?.size_bytes ?? release.size_bytes,
      reason: typeof match.reason === "string" ? match.reason : null,
    };
  }, [preview, files, release.filename, release.size_bytes]);

  const startMutation = useMutation({
    mutationFn: (options: {
      file_paths?: string[];
      allow_pack?: boolean;
    }) =>
      startDownload(releaseId ?? release.release_id ?? "", {
        episode_id: episodeId,
        ...options,
      }),
    onSuccess: () => {
      onQueued(`Queued ${episodeLabel} for download.`);
      setPhase("idle");
      setPreview(null);
      setChooser(null);
      setSelectedPath(null);
      setPackConfirmed(false);
      void queryClient.invalidateQueries({ queryKey: ["downloads"] });
    },
    onError: (cause) => {
      setError(cause instanceof ApiError ? cause.message : "The download could not be queued.");
      setPhase("error");
    },
  });

  async function begin() {
    setError(null);
    setPhase("loading");
    try {
      const id = releaseId ?? (await ensureRelease());
      setReleaseId(id);
      const result = await previewSelection(id, { episode_id: episodeId });
      setPreview(result);
      setPhase("confirm");
    } catch (cause) {
      const detail = detailObject(cause);
      const candidates = detailFiles(detail.candidates);
      const all = detailFiles(detail.files);
      const code = cause instanceof ApiError ? cause.code : null;
      if (code === "FILE_SELECTION_REQUIRED" || code === "EPISODE_FILE_NOT_FOUND") {
        const merged = [...all];
        for (const candidate of candidates) {
          if (!merged.some((item) => item.index === candidate.index)) {
            merged.push(candidate);
          }
        }
        setChooser({
          files: merged,
          candidates,
          label:
            typeof detail.episode_label === "string" ? detail.episode_label : episodeLabel,
        });
        setSelectedPath(candidates[0]?.path ?? all[0]?.path ?? null);
        setPhase("choose");
        return;
      }
      setError(cause instanceof ApiError ? cause.message : "The file list could not be loaded.");
      setPhase("error");
    }
  }

  return (
    <div className="flex w-full flex-col gap-2" data-testid="episode-download">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={phase === "loading" || startMutation.isPending}
          onClick={() => void begin()}
          className="min-h-control-sm rounded-control border border-border-strong px-3 text-small font-semibold text-fg hover:bg-hover disabled:opacity-40"
        >
          {phase === "loading" ? "Checking files…" : `Download ${episodeLabel}`}
        </button>
      </div>

      {phase === "confirm" && confirmed ? (
        <div
          role="status"
          className="flex flex-col gap-2 rounded-control border border-border-subtle bg-canvas/60 p-2 text-small"
        >
          <p className="break-words text-fg">
            Will download: <span className="font-mono">{confirmed.path}</span>
            {confirmed.size ? ` (${formatBytes(confirmed.size)})` : ""}
            {confirmed.reason ? (
              <span className="text-fg-comment"> · {confirmed.reason}</span>
            ) : null}
          </p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => {
                setChooser({
                  files: preview?.files ?? [],
                  candidates: [],
                  label: episodeLabel,
                });
                setSelectedPath(confirmed.path);
                setPackConfirmed(false);
                setPhase("choose");
              }}
              className="min-h-control-sm rounded-control border border-border-strong px-panel-compact text-small font-semibold text-fg hover:bg-hover"
            >
              Change
            </button>
            <button
              type="button"
              disabled={startMutation.isPending}
              onClick={() =>
                startMutation.mutate(
                  preview?.selected_files === null
                    ? {}
                    : { file_paths: [confirmed.path] },
                )
              }
              className="min-h-control-sm rounded-control border border-accent/50 bg-accent/15 px-panel-compact text-small font-semibold text-accent hover:bg-accent/25 disabled:opacity-40"
            >
              {startMutation.isPending ? "Queueing…" : "Download"}
            </button>
          </div>
        </div>
      ) : null}

      {phase === "choose" && chooser ? (
        <div
          role="dialog"
          aria-label={`Choose a file for ${episodeLabel}`}
          className="flex flex-col gap-2 rounded-control border border-border-subtle bg-canvas/60 p-2"
        >
          <p className="text-small font-semibold text-fg">
            Choose the file for {chooser.label}
          </p>
          <ul className="flex flex-col gap-1">
            {chooser.files.map((file) => {
              const reason = chooser.candidates.find(
                (candidate) => candidate.index === file.index,
              )?.reason;
              return (
                <li key={`${file.index}-${file.path}`}>
                  <label className="flex min-h-control-sm cursor-pointer items-start gap-2 rounded-control px-1 py-1 text-small hover:bg-hover">
                    <input
                      type="radio"
                      name={`episode-file-${release.info_hash}`}
                      className="mt-1 shrink-0"
                      aria-label={`Choose ${file.path}`}
                      checked={selectedPath === file.path}
                      onChange={() => setSelectedPath(file.path)}
                    />
                    <span className="flex min-w-0 flex-col">
                      <span className="break-words font-mono text-fg">{file.path}</span>
                      <span className="text-micro text-fg-comment">
                        {formatBytes(file.size_bytes)}
                        {reason ? ` · ${reason}` : ""}
                      </span>
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={!selectedPath || startMutation.isPending}
              onClick={() =>
                selectedPath && startMutation.mutate({ file_paths: [selectedPath] })
              }
              className="min-h-control-sm rounded-control border border-accent/50 bg-accent/15 px-panel-compact text-small font-semibold text-accent hover:bg-accent/25 disabled:opacity-40"
            >
              Download selected file
            </button>
            <button
              type="button"
              onClick={() => {
                setPhase("idle");
                setChooser(null);
              }}
              className="min-h-control-sm rounded-control px-panel-compact text-small font-semibold text-fg-muted hover:bg-hover"
            >
              Cancel
            </button>
          </div>
          <div className="flex flex-col gap-2 border-t border-border-subtle pt-2">
            <label className="flex items-start gap-2 text-small text-fg-muted">
              <input
                type="checkbox"
                className="mt-1 shrink-0"
                checked={packConfirmed}
                onChange={(event) => setPackConfirmed(event.target.checked)}
              />
              I understand this downloads every file in the pack.
            </label>
            <button
              type="button"
              disabled={!packConfirmed || startMutation.isPending}
              onClick={() => startMutation.mutate({ allow_pack: true })}
              className="min-h-control-sm w-fit rounded-control border border-warning/50 px-panel-compact text-small font-semibold text-warning hover:bg-warning/10 disabled:opacity-40"
            >
              Download whole pack
            </button>
          </div>
        </div>
      ) : null}

      {phase === "error" && error ? (
        <div role="alert" className="flex flex-col gap-2 text-small text-danger">
          <p className="break-words">{error}</p>
          <button
            type="button"
            onClick={() => setPhase("idle")}
            className="min-h-control-sm w-fit rounded-control border border-border-strong px-panel-compact text-small font-semibold text-fg hover:bg-hover"
          >
            Close
          </button>
        </div>
      ) : null}
    </div>
  );
}
