import { memo } from "react";

import type { DownloadAction, DownloadRecord } from "../lib/api";
import { formatBytes, formatEta, formatSpeed, percent } from "../lib/format";

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
        "min-h-8 rounded-control border px-3 text-[12px] font-semibold transition-colors",
        primary
          ? "border-accent/50 bg-accent/15 text-accent hover:bg-accent/25"
          : "border-border-subtle text-fg-muted hover:bg-hover hover:text-fg",
      ].join(" ")}
    >
      {actionLabel(action)}
    </button>
  );
}

export const DownloadRow = memo(function DownloadRow({
  download,
  onAction,
}: {
  download: DownloadRecord;
  onAction: (id: string, action: DownloadAction) => void;
}) {
  const value = percent(download.progress);
  const label = STATUS_LABEL[download.status] ?? download.status;
  const name = download.title ?? download.filename ?? download.id;
  const active = isActiveDownload(download);
  const failed = isFailedDownload(download);
  const completed = isCompletedDownload(download);

  return (
    <article
      data-testid={`download-row-${download.id}`}
      className="rounded-panel border border-border-subtle bg-panel p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-[15px] font-semibold leading-5 text-fg">{name}</h3>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-fg-muted">
            <span
              className={[
                "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium leading-4",
                failed ? "bg-danger/15 text-danger" : "bg-hover text-fg-muted",
              ].join(" ")}
            >
              {label}
            </span>
            {download.episode_label ? <span>{download.episode_label}</span> : null}
            {download.quality ? <span>{download.quality}</span> : null}
            <span>{download.engine === "aria2" ? "Direct" : "Torrent"}</span>
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {download.status === "active" ? (
            <RowButton action="pause" onAction={onAction} id={download.id} />
          ) : null}
          {download.status === "paused" ? (
            <RowButton action="resume" onAction={onAction} id={download.id} primary />
          ) : null}
          {active || failed ? (
            <RowButton action="cancel" onAction={onAction} id={download.id} />
          ) : null}
          {failed ? (
            <RowButton action="retry" onAction={onAction} id={download.id} primary />
          ) : null}
        </div>
      </div>

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
        <div className="mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-[12px] text-fg-muted">
          <span className="tabular-nums">
            {formatBytes(download.downloaded_bytes)} / {formatBytes(download.total_bytes)} ·{" "}
            {Math.round(value)}%
          </span>
          <span className="flex gap-3 tabular-nums">
            {active ? (
              <>
                <span>{formatSpeed(download.speed_bps)}</span>
                <span>ETA {formatEta(download.eta_seconds)}</span>
              </>
            ) : null}
          </span>
        </div>
      </div>

      {download.error ? (
        <p className="mt-2 rounded-control border-l-2 border-danger bg-canvas px-3 py-2 text-[12px] leading-5 text-danger">
          {download.error}
        </p>
      ) : null}
    </article>
  );
});
