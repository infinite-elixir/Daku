import type { WatchStatus, WatchStatusFilter } from "./api";

export const WATCH_STATUS_LABELS: Record<WatchStatusFilter, string> = {
  planning: "Planning",
  watching: "Watching",
  completed: "Completed",
  dropped: "Dropped",
  on_hold: "On hold",
  untracked: "Untracked",
};

export const WATCH_STATUS_OPTIONS: { value: WatchStatus | ""; label: string }[] = [
  { value: "", label: "Untracked" },
  { value: "planning", label: "Planning" },
  { value: "watching", label: "Watching" },
  { value: "completed", label: "Completed" },
  { value: "dropped", label: "Dropped" },
  { value: "on_hold", label: "On hold" },
];

export const LIBRARY_STATUS_FILTERS: WatchStatusFilter[] = [
  "planning",
  "watching",
  "completed",
  "dropped",
  "on_hold",
  "untracked",
];

export function watchStatusLabel(status: WatchStatus | null | undefined): string {
  return status ? WATCH_STATUS_LABELS[status] : WATCH_STATUS_LABELS.untracked;
}
