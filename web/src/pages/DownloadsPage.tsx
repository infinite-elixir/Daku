import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { Link } from "react-router-dom";

import { DownloadRow, isActiveDownload, isCompletedDownload, isFailedDownload } from "../components/DownloadRow";
import { DOWNLOADS_QUERY_KEY, useEventSource } from "../hooks/useEventSource";
import { formatBytes } from "../lib/format";
import {
  downloadAction,
  listDownloads,
  removeDownload,
  type DownloadAction,
  type DownloadRecord,
  type DownloadsResponse,
} from "../lib/api";

const OPTIMISTIC_STATUS: Record<DownloadAction, string> = {
  pause: "paused",
  resume: "active",
  cancel: "cancelled",
  retry: "queued",
};

function withStatus(
  current: DownloadsResponse | undefined,
  id: string,
  status: string,
): DownloadsResponse | undefined {
  if (!current) {
    return current;
  }
  let changed = false;
  const items = current.items.map((item) => {
    if (item.id !== id) {
      return item;
    }
    changed = true;
    return { ...item, status, error: status === "queued" ? null : item.error };
  });
  return changed ? { ...current, items } : current;
}

function withoutDownload(
  current: DownloadsResponse | undefined,
  id: string,
): DownloadsResponse | undefined {
  if (!current) {
    return current;
  }
  const items = current.items.filter((item) => item.id !== id);
  if (items.length === current.items.length) {
    return current;
  }
  return { items, total: items.length };
}

export function DownloadsPage() {
  const queryClient = useQueryClient();
  const stream = useEventSource();
  const [removeError, setRemoveError] = useState<string | null>(null);

  const { data, error, isLoading, refetch } = useQuery<DownloadsResponse>({
    queryKey: DOWNLOADS_QUERY_KEY,
    queryFn: ({ signal }) => listDownloads(signal),
    staleTime: 5_000,
  });

  const mutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: DownloadAction }) =>
      downloadAction(id, action),
    onMutate: async ({ id, action }) => {
      await queryClient.cancelQueries({ queryKey: DOWNLOADS_QUERY_KEY });
      const previous = queryClient.getQueryData<DownloadsResponse>(DOWNLOADS_QUERY_KEY);
      queryClient.setQueryData<DownloadsResponse>(DOWNLOADS_QUERY_KEY, (current) =>
        withStatus(current, id, OPTIMISTIC_STATUS[action]),
      );
      return { previous };
    },
    onError: (_error, _variables, context) => {
      if (context?.previous) {
        queryClient.setQueryData(DOWNLOADS_QUERY_KEY, context.previous);
      }
    },
    onSuccess: (updated: DownloadRecord) => {
      queryClient.setQueryData<DownloadsResponse>(DOWNLOADS_QUERY_KEY, (current) => {
        if (!current) {
          return current;
        }
        const items = current.items.map((item) =>
          item.id === updated.id ? { ...item, ...updated } : item,
        );
        return { ...current, items };
      });
    },
  });

  const onAction = useCallback(
    (id: string, action: DownloadAction) => {
      mutation.mutate({ id, action });
    },
    [mutation],
  );

  const removeMutation = useMutation({
    mutationFn: ({ id, deleteFiles }: { id: string; deleteFiles: boolean }) =>
      removeDownload(id, { deleteFiles }),
    onMutate: async ({ id }) => {
      setRemoveError(null);
      await queryClient.cancelQueries({ queryKey: DOWNLOADS_QUERY_KEY });
      const previous = queryClient.getQueryData<DownloadsResponse>(DOWNLOADS_QUERY_KEY);
      queryClient.setQueryData<DownloadsResponse>(DOWNLOADS_QUERY_KEY, (current) =>
        withoutDownload(current, id),
      );
      return { previous };
    },
    onError: (error, _variables, context) => {
      if (context?.previous) {
        queryClient.setQueryData(DOWNLOADS_QUERY_KEY, context.previous);
      }
      setRemoveError(error.message);
    },
    onSuccess: () => setRemoveError(null),
  });

  const onRemove = useCallback(
    (id: string, deleteFiles: boolean) => {
      removeMutation.mutate({ id, deleteFiles });
    },
    [removeMutation],
  );

  const rows = data?.items ?? [];
  const active = rows.filter(isActiveDownload);
  const completed = rows.filter(isCompletedDownload);
  const failed = rows.filter(isFailedDownload);

  return (
    <section className="flex flex-col gap-section">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-h1 font-bold text-fg">Downloads</h1>
          <p className="text-small text-fg-muted">
            {data ? `${data.total} ${data.total === 1 ? "download" : "downloads"}` : "Queue and progress"}
          </p>
        </div>
        <span
          className={[
            "inline-flex items-center gap-2 rounded-full px-3 py-1 text-micro font-medium",
            stream.connected
              ? "bg-accent/15 text-accent"
              : stream.polling
                ? "bg-hover text-fg-muted"
                : "bg-hover text-fg-muted",
          ].join(" ")}
        >
          <span
            aria-hidden
            className={[
              "h-1.5 w-1.5 rounded-full",
              stream.connected ? "bg-accent" : "bg-fg-muted",
            ].join(" ")}
          />
          {stream.connected ? "Live" : stream.polling ? "Polling every 3s" : "Reconnecting…"}
        </span>
      </header>

      {removeError ? (
        <div
          role="alert"
          className="rounded-panel border-l-2 border-danger bg-panel px-panel py-panel-compact text-small text-danger"
        >
          {removeError}
        </div>
      ) : null}

      {error ? (
        <div className="rounded-panel border-l-2 border-danger bg-panel px-panel py-panel-compact">
          <p className="text-small text-danger">{error.message}</p>
          <button
            type="button"
            onClick={() => void refetch()}
            className="mt-2 min-h-control-sm rounded-control border border-border-strong px-3 text-small font-semibold text-fg hover:bg-hover"
          >
            Retry
          </button>
        </div>
      ) : isLoading ? (
        <p className="text-small text-fg-muted">Loading downloads…</p>
      ) : rows.length === 0 ? (
        <div className="rounded-panel border border-border-subtle bg-panel px-6 py-12 text-center">
          <p className="text-h2 font-semibold tracking-tight text-fg">Nothing downloading</p>
          <p className="mx-auto mt-1 max-w-[48ch] text-small text-fg-muted">
            Pick a release from a title and it will appear here with live progress.
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-section">
          <DownloadSection
            title="Active"
            rows={active}
            onAction={onAction}
            onRemove={onRemove}
            empty="No active downloads."
          />
          <DownloadSection
            title="Completed"
            rows={completed}
            onAction={onAction}
            onRemove={onRemove}
            empty="Nothing completed yet."
          />
          {failed.length > 0 ? (
            <DownloadSection
              title="Needs attention"
              rows={failed}
              onAction={onAction}
              onRemove={onRemove}
              empty=""
            />
          ) : null}
        </div>
      )}
    </section>
  );
}

interface DownloadGroup {
  titleId: string | null;
  title: string;
  rows: DownloadRecord[];
}

function groupByTitle(rows: DownloadRecord[]): DownloadGroup[] {
  const groups = new Map<string, DownloadGroup>();
  for (const row of rows) {
    const key = row.title_id ?? `download:${row.id}`;
    const group = groups.get(key) ?? {
      titleId: row.title_id,
      title: row.title ?? row.filename ?? row.id,
      rows: [],
    };
    group.rows.push(row);
    groups.set(key, group);
  }
  const result = [...groups.values()];
  for (const group of result) {
    group.rows.sort((left, right) => left.created_at.localeCompare(right.created_at));
  }
  result.sort((left, right) => {
    const leftAt = left.rows.at(-1)?.updated_at ?? "";
    const rightAt = right.rows.at(-1)?.updated_at ?? "";
    return rightAt.localeCompare(leftAt);
  });
  return result;
}

function DownloadSection({
  title,
  rows,
  onAction,
  onRemove,
  empty,
}: {
  title: string;
  rows: DownloadRecord[];
  onAction: (id: string, action: DownloadAction) => void;
  onRemove: (id: string, deleteFiles: boolean) => void;
  empty: string;
}) {
  const groups = groupByTitle(rows);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-h2 font-semibold tracking-tight text-fg">{title}</h2>
        <span className="text-small tabular-nums text-fg-muted">{rows.length}</span>
      </div>
      {rows.length === 0 ? (
        empty ? <p className="text-small text-fg-muted">{empty}</p> : null
      ) : (
        <div className="flex flex-col gap-panel">
          {groups.map((group) => {
            const files = group.rows.flatMap((row) => row.downloaded_files ?? []);
            const size = files.reduce((total, file) => total + (file.size_bytes ?? 0), 0);
            return (
              <section key={group.titleId ?? group.title} className="flex flex-col gap-3">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  {group.titleId ? (
                    <Link
                      to={`/title/${group.titleId}`}
                      className="text-title font-semibold text-fg hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                    >
                      {group.title}
                    </Link>
                  ) : (
                    <h3 className="text-title font-semibold text-fg">{group.title}</h3>
                  )}
                  {group.titleId && files.length > 0 ? (
                    <Link
                      to={`/title/${group.titleId}/files`}
                      className="text-small font-medium text-fg-muted hover:text-fg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                    >
                      Files ({files.length})
                    </Link>
                  ) : null}
                  {size > 0 ? (
                    <span className="ml-auto text-small tabular-nums text-fg-muted">
                      {formatBytes(size)}
                    </span>
                  ) : null}
                </div>
                <ul className="flex flex-col gap-3">
                  {group.rows.map((row) => (
                    <li key={row.id}>
                      <DownloadRow download={row} onAction={onAction} onRemove={onRemove} />
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
