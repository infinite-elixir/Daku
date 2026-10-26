import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import {
  ApiError,
  getReleaseFiles,
  inspectMagnet,
  startDownload,
  type MagnetMetadata,
} from "../lib/api";
import { formatBytes } from "../lib/format";
import { CopyMagnet, shortMagnet } from "./CopyMagnet";

const PROBE_TIMEOUT_MS = 30_000;

export function magnetHash(magnet: string | null | undefined): string {
  if (!magnet) {
    return "none";
  }
  const match = /[?&]xt=urn:btih:([a-zA-Z0-9]+)/i.exec(magnet);
  return match?.[1]?.toLowerCase() ?? "unknown";
}

export function looksLikeMagnet(value: string | null | undefined): boolean {
  if (!value || !/^\s*magnet:/i.test(value)) {
    return false;
  }
  const matches = value.match(/xt=urn:btih:([a-zA-Z0-9]+)/gi) ?? [];
  if (matches.length !== 1) {
    return false;
  }
  const hash = matches[0].split(":").pop() ?? "";
  return /^[0-9a-f]{40}$/i.test(hash) || /^[a-z2-7]{32}$/i.test(hash);
}

async function fetchFiles(
  magnet: string | null,
  releaseId: string | null,
  signal: AbortSignal,
  controller: AbortController,
  timedOut: { current: boolean },
  timeoutMs: number,
): Promise<MagnetMetadata> {
  const onAbort = () => controller.abort();
  signal.addEventListener("abort", onAbort);
  const timer = setTimeout(() => {
    timedOut.current = true;
    controller.abort();
  }, timeoutMs);
  try {
    return releaseId
      ? await getReleaseFiles(releaseId, controller.signal)
      : await inspectMagnet(magnet ?? "", controller.signal);
  } catch (error) {
    if (timedOut.current) {
      throw new ApiError(
        "The magnet did not return metadata within 30 seconds.",
        504,
        "PROBE_TIMEOUT",
      );
    }
    throw error;
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", onAbort);
  }
}

function FileList({
  metadata,
  selected,
  onToggle,
}: {
  metadata: MagnetMetadata;
  selected: Set<number>;
  onToggle: (index: number) => void;
}) {
  return (
    <>
      <div className="hidden w-full sm:block">
        <table className="w-full table-fixed text-left text-[12px]">
          <thead>
            <tr className="border-b border-border-subtle text-fg-comment">
              <th className="w-8 py-1 pr-1 font-medium">
                <span className="sr-only">Select</span>
              </th>
              <th className="w-10 py-1 pr-2 font-medium">#</th>
              <th className="py-1 pr-2 font-medium">Path</th>
              <th className="w-24 py-1 text-right font-medium">Size</th>
            </tr>
          </thead>
          <tbody>
            {metadata.files.map((file) => (
              <tr key={`${file.index}-${file.path}`} className="border-b border-border-subtle/60">
                <td className="py-1.5 pr-1 align-top">
                  <input
                    type="checkbox"
                    aria-label={`Select ${file.path}`}
                    checked={selected.has(file.index)}
                    onChange={() => onToggle(file.index)}
                  />
                </td>
                <td className="py-1.5 pr-2 align-top font-mono tabular-nums text-fg-comment">
                  {file.index}
                </td>
                <td className="min-w-0 break-words py-1.5 pr-2 align-top font-mono text-fg" title={file.path}>
                  {file.path}
                </td>
                <td className="whitespace-nowrap py-1.5 text-right align-top tabular-nums text-fg-muted">
                  {formatBytes(file.size_bytes)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="divide-y divide-border-subtle rounded-control border border-border-subtle sm:hidden">
        {metadata.files.map((file) => (
          <li key={`${file.index}-${file.path}-stacked`} className="flex items-start gap-2 px-2 py-1.5">
            <input
              type="checkbox"
              className="mt-1 shrink-0"
              aria-label={`Select ${file.path}`}
              checked={selected.has(file.index)}
              onChange={() => onToggle(file.index)}
            />
            <span className="flex min-w-0 flex-col gap-0.5">
              <span className="break-words font-mono text-[12px] text-fg" title={file.path}>
                {file.path}
              </span>
              <span className="text-micro tabular-nums text-fg-comment">
                #{file.index} · {formatBytes(file.size_bytes)}
              </span>
            </span>
          </li>
        ))}
      </ul>
    </>
  );
}

export function MagnetFiles({
  magnet,
  releaseId = null,
  episodeId = null,
  buttonLabel = "Files",
  timeoutMs = PROBE_TIMEOUT_MS,
  onQueued,
}: {
  magnet: string | null | undefined;
  releaseId?: string | null;
  episodeId?: string | null;
  buttonLabel?: string;
  timeoutMs?: number;
  onQueued?: (message: string) => void;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const controllerRef = useRef<AbortController | null>(null);
  const timeoutFlag = useRef(false);

  const downloadMutation = useMutation({
    mutationFn: (paths: string[]) =>
      startDownload(releaseId ?? "", {
        episode_id: episodeId,
        file_paths: paths,
      }),
    onSuccess: () => {
      onQueued?.("Queued the selected files for download.");
      setSelected(new Set());
      void queryClient.invalidateQueries({ queryKey: ["downloads"] });
    },
  });

  function toggle(index: number) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  }

  const enabled = Boolean(magnet || releaseId);
  const query = useQuery({
    queryKey: ["magnet-files", releaseId ?? magnetHash(magnet), attempt],
    queryFn: async ({ signal }) => {
      controllerRef.current = new AbortController();
      timeoutFlag.current = false;
      return fetchFiles(
        magnet ?? null,
        releaseId,
        signal,
        controllerRef.current,
        timeoutFlag,
        timeoutMs,
      );
    },
    enabled: open && enabled,
    retry: false,
    staleTime: 24 * 60 * 60 * 1000,
    gcTime: 24 * 60 * 60 * 1000,
  });

  function cancel() {
    controllerRef.current?.abort();
    setOpen(false);
  }

  function retry() {
    setAttempt((value) => value + 1);
  }

  const error = query.error;
  const code =
    error instanceof ApiError
      ? (error.code ?? `HTTP_${error.status}`)
      : error
        ? "PROBE_FAILED"
        : null;

  return (
    <div
      id={releaseId ? `release-files-${releaseId}` : undefined}
      className="flex w-full flex-col gap-2"
    >
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          aria-expanded={open}
          disabled={!enabled}
          title={enabled ? undefined : "This release has no magnet to inspect"}
          onClick={() => {
            if (open) {
              cancel();
            } else {
              setOpen(true);
            }
          }}
          className="min-h-8 rounded-control border border-border-strong px-2.5 text-[12px] font-semibold text-fg hover:bg-hover disabled:opacity-40"
        >
          {buttonLabel}
        </button>
        {open && enabled ? <CopyMagnet magnet={magnet} compact /> : null}
      </div>

      {open && enabled ? (
        <div className="rounded-control border border-border-subtle bg-canvas/60 p-3">
          {query.isPending ? (
            <div className="flex flex-wrap items-center justify-between gap-2 text-[12px] text-fg-muted">
              <span>Reading torrent metadata…</span>
              <button
                type="button"
                onClick={cancel}
                className="min-h-7 rounded-control border border-border-strong px-2 text-[12px] font-semibold text-fg hover:bg-hover"
              >
                Cancel
              </button>
            </div>
          ) : null}

          {error ? (
            <div className="flex flex-col gap-2">
              <p role="alert" className="text-[12px] text-danger">
                {timeoutFlag.current
                  ? "The magnet timed out after 30 seconds."
                  : error instanceof ApiError
                    ? error.message
                    : "The file list could not be loaded."}
              </p>
              {code ? <p className="text-micro text-fg-comment">{code}</p> : null}
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={retry}
                  className="min-h-8 rounded-control border border-border-strong px-2.5 text-[12px] font-semibold text-fg hover:bg-hover"
                >
                  Retry
                </button>
                <button
                  type="button"
                  onClick={cancel}
                  className="min-h-8 rounded-control px-2.5 text-[12px] font-semibold text-fg-muted hover:bg-hover"
                >
                  Close
                </button>
              </div>
            </div>
          ) : null}

          {query.data ? (
            <div className="flex flex-col gap-2">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <p className="min-w-0 break-words text-[13px] font-semibold text-fg">
                  {query.data.name || shortMagnet(magnet)}
                </p>
                <span className="text-[12px] text-fg-muted">
                  {formatBytes(query.data.total_size_bytes)} · {query.data.files.length}{" "}
                  {query.data.files.length === 1 ? "file" : "files"}
                </span>
                {query.data.cached ? (
                  <span className="rounded-full bg-hover px-2 py-0.5 text-micro text-fg-muted">
                    cached
                  </span>
                ) : null}
              </div>
              <FileList metadata={query.data} selected={selected} onToggle={toggle} />
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  disabled={
                    selected.size === 0 ||
                    !releaseId ||
                    downloadMutation.isPending
                  }
                  title={
                    !releaseId
                      ? "Save this release before downloading individual files"
                      : selected.size === 0
                        ? "Tick at least one file"
                        : undefined
                  }
                  onClick={() => {
                    const paths = query.data.files
                      .filter((file) => selected.has(file.index))
                      .map((file) => file.path);
                    if (paths.length > 0) {
                      downloadMutation.mutate(paths);
                    }
                  }}
                  className="min-h-8 rounded-control border border-border-strong px-2.5 text-[12px] font-semibold text-fg hover:bg-hover disabled:opacity-40"
                >
                  {downloadMutation.isPending ? "Queueing…" : "Download selected"}
                </button>
                <span className="text-micro text-fg-comment">
                  {!releaseId
                    ? "Save this release to download individual files."
                    : selected.size === 0
                      ? "Tick at least one file to download a subset."
                      : `${selected.size} file${selected.size === 1 ? "" : "s"} selected`}
                </span>
              </div>
              {downloadMutation.error ? (
                <p role="alert" className="text-[12px] text-danger">
                  {downloadMutation.error instanceof ApiError
                    ? downloadMutation.error.message
                    : "The selected files could not be queued."}
                </p>
              ) : null}
              <p className="text-micro text-fg-comment">Metadata only — nothing was downloaded</p>
              <p className="text-micro text-fg-comment">Source: probe</p>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
