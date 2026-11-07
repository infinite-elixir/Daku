import { Fragment, memo, useCallback, useState } from "react";

import type { DownloadAction, DownloadRecord } from "../lib/api";
import { formatBytes, formatEta, formatSpeed, percent } from "../lib/format";
import { RemoveDownloadDialog } from "./RemoveDownloadDialog";

const STATUS_LABEL: Record<string, string> = {
  queued: "Queued",
  active: "Downloading",
  paused: "Paused",
  complete: "Complete",
  organizing: "Organizing",
  available: "Available",
  failed: "Failed",
  cancelled: "Cancelled",
};

const ACTIVE_STATUSES = new Set(["queued", "active", "paused", "organizing"]);

export function isActiveDownload(row: DownloadRecord): boolean {
  return ACTIVE_STATUSES.has(row.status);
}

export function isCompletedDownload(row: DownloadRecord): boolean {
  return row.status === "complete" || row.status === "available";
}

export function isFailedDownload(row: DownloadRecord): boolean {
  return row.status === "failed" || row.status === "cancelled";
}

function actionLabel(action: DownloadAction): string {
  switch (action) {
    case "pause":
      return "Pause";
    case "resume":
      return "Resume";
    case "cancel":
      return "Cancel";
    case "retry":
      return "Retry";
  }
}

function RowButton({
  action,
  onAction,
  id,
  primary,
}: {
  action: DownloadAction;
  onAction: (id: string, action: DownloadAction) => void;
  id: string;
  primary?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={() => onAction(id, action)}
      className={[
        "inline-flex min-h-control-sm w-full items-center justify-center rounded-control border px-3 text-small font-semibold transition-colors sm:w-auto",
        primary
          ? "border-accent/50 bg-accent/15 text-accent hover:bg-accent/25"
          : "border-border-subtle text-fg-muted hover:bg-hover hover:text-fg",
      ].join(" ")}
    >
      {actionLabel(action)}
    </button>
  );
}

function libraryNames(files: NonNullable<DownloadRecord["downloaded_files"]>): {
  shown: string[];
  extra: number;
} {
  const names = files.map((file) => file.episode_label ?? file.file_name);
  return { shown: names.slice(0, 3), extra: Math.max(0, names.length - 3) };
}

export const DownloadRow = memo(function DownloadRow({
  download,
  onAction,
  onRemove,
}: {
  download: DownloadRecord;
  onAction: (id: string, action: DownloadAction) => void;
  onRemove: (id: string, deleteFiles: boolean) => void;
}) {
  const [confirmingRemove, setConfirmingRemove] = useState(false);
  const closeRemove = useCallback(() => setConfirmingRemove(false), []);
  const confirmRemove = useCallback(
    (deleteFiles: boolean) => {
      setConfirmingRemove(false);
      onRemove(download.id, deleteFiles);
    },
    [download.id, onRemove],
  );
  const value = percent(download.progress);
  const label = STATUS_LABEL[download.status] ?? download.status;
  const name = download.title ?? download.filename ?? download.id;
  const active = isActiveDownload(download);
  const failed = isFailedDownload(download);
  const completed = isCompletedDownload(download);
  const engineLabel = download.engine === "aria2" ? "Direct" : "Torrent";
  const facts = [download.episode_label, download.quality, engineLabel].filter(
    (fact): fact is string => Boolean(fact),
  );
  const meta = facts.join(" · ");
  const selectionSize =
    typeof download.selection_size_bytes === "number" && download.selection_size_bytes > 0
      ? download.selection_size_bytes
      : null;
  const hasSelection = Boolean(download.selection_label) || selectionSize !== null;
  const selectionSubject =
    download.selection_label === "Whole pack"
      ? "Whole pack"
      : (download.episode_label ??
        download.selection_label ??
        download.filename ??
        "selected files");
  const fetchingText = hasSelection
    ? `Fetching ${selectionSubject}${
        selectionSize !== null ? ` · ${formatBytes(selectionSize)}` : ""
      }`
    : null;
  const progressText = completed
    ? `Downloaded ${formatBytes(download.total_bytes)} · ${Math.round(value)}%`
    : `Downloading ${formatBytes(download.downloaded_bytes)} of ${formatBytes(
        download.total_bytes,
      )} · ${Math.round(value)}%`;
  const numbersText = fetchingText ?? progressText;
  const errorText =
    download.error && download.error.includes("FILE_SELECTION_FAILED")
      ? `Could not restrict this torrent to ${
          download.episode_label ?? "the selected files"
        }. Nothing is downloading.`
      : download.error;
  const downloadedFiles = download.downloaded_files ?? [];
  const librarySize = downloadedFiles.reduce(
    (total, file) => total + (file.size_bytes ?? 0),
    0,
  );
  const sameSingleFile =
    downloadedFiles.length === 1 &&
    (!hasSelection ||
      (selectionSize !== null && selectionSize === downloadedFiles[0]?.size_bytes));
  const showLibrary = downloadedFiles.length > 0 && !sameSingleFile;
  const library = libraryNames(downloadedFiles);
  const fullLibraryTitle = downloadedFiles
    .map(
      (file) =>
        `${file.episode_label ?? file.file_name}${
          file.size_bytes ? ` · ${formatBytes(file.size_bytes)}` : ""
        }`,
    )
    .join("\n");

  return (
    <article
      data-testid={`download-row-${download.id}`}
      className="min-w-0 rounded-panel border border-border-subtle bg-panel p-panel"
    >
      <div className="flex min-w-0 items-center justify-between gap-3">
        <h4
          className="min-w-0 flex-1 truncate text-title font-semibold text-fg"
          title={name}
        >
          {name}
        </h4>
        <span
          className={[
            "inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-micro font-medium",
            failed ? "bg-danger/15 text-danger" : "bg-hover text-fg-muted",
          ].join(" ")}
        >
          {label}
        </span>
      </div>

      {meta ? (
        <p className="mt-0.5 truncate text-small text-fg-muted" title={meta}>
          {facts.map((fact, index) => (
            <Fragment key={`${fact}-${index}`}>
              {index > 0 ? " · " : null}
              <span>{fact}</span>
            </Fragment>
          ))}
        </p>
      ) : null}

      <div className="mt-3">
        <div
          role="progressbar"
          aria-label={`${name} progress`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(value)}
          className="h-1.5 w-full overflow-hidden rounded-full bg-hover"
        >
          <div
            className={[
              "h-full rounded-full transition-[width] duration-500",
              failed ? "bg-danger" : completed ? "bg-accent" : "bg-accent/80",
            ].join(" ")}
            style={{ width: `${value}%` }}
          />
        </div>
        <div className="mt-2 flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-1 text-small">
          <span
            className={[
              "min-w-0 tabular-nums",
              fetchingText && download.status === "active"
                ? "text-accent"
                : "text-fg-muted",
            ].join(" ")}
          >
            {numbersText}
          </span>
          <span className="flex shrink-0 gap-3 tabular-nums text-fg-muted">
            {download.status === "active" ? (
              <>
                <span>{formatSpeed(download.speed_bps)}</span>
                <span>ETA {formatEta(download.eta_seconds)}</span>
              </>
            ) : null}
          </span>
        </div>
      </div>

      {showLibrary ? (
        <p
          className="mt-1.5 min-w-0 truncate text-small text-fg-muted"
          title={fullLibraryTitle}
        >
          In library:{" "}
          <span className="tabular-nums">
            {formatBytes(librarySize)} · {downloadedFiles.length}{" "}
            {downloadedFiles.length === 1 ? "file" : "files"}
          </span>{" "}
          <span>
            ·{" "}
            {library.shown.map((name, index) => (
              <Fragment key={`${name}-${index}`}>
                {index > 0 ? ", " : null}
                <span>{name}</span>
              </Fragment>
            ))}
            {library.extra > 0 ? ` +${library.extra}` : ""}
          </span>
        </p>
      ) : null}

      {errorText ? (
        <p className="mt-2 rounded-control border-l-2 border-danger bg-canvas px-3 py-2 text-small text-danger">
          {errorText}
        </p>
      ) : null}

      <div className="mt-3 flex w-full flex-col gap-2 sm:flex-row sm:justify-end">
        {download.status === "active" ? (
          <RowButton action="pause" onAction={onAction} id={download.id} primary />
        ) : null}
        {download.status === "paused" ? (
          <RowButton action="resume" onAction={onAction} id={download.id} primary />
        ) : null}
        {failed ? (
          <RowButton action="retry" onAction={onAction} id={download.id} primary />
        ) : null}
        {active || failed ? (
          <RowButton action="cancel" onAction={onAction} id={download.id} />
        ) : null}
        <button
          type="button"
          onClick={() => setConfirmingRemove(true)}
          aria-label="Remove download"
          title="Remove download"
          className="inline-flex min-h-control-sm w-full items-center justify-center gap-1.5 rounded-control border border-border-subtle px-panel-compact text-small font-semibold text-fg-muted hover:bg-hover hover:text-danger sm:w-auto"
        >
          <svg
            aria-hidden
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="h-4 w-4"
          >
            <path d="M3.5 5.5h13M8 3.5h4M5.5 5.5l.7 10a1.5 1.5 0 0 0 1.5 1.4h4.6a1.5 1.5 0 0 0 1.5-1.4l.7-10M8.3 8.5v5.4M11.7 8.5v5.4" />
          </svg>
          <span className="hidden sm:inline">Remove</span>
        </button>
      </div>

      {confirmingRemove ? (
        <RemoveDownloadDialog
          name={name}
          showDeleteFiles={download.can_delete_source_files === true}
          onCancel={closeRemove}
          onConfirm={confirmRemove}
        />
      ) : null}
    </article>
  );
});
