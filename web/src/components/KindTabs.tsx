import type { Kind } from "../lib/api";

export type KindFilter = Kind | "all";

const OPTIONS: Array<{ value: KindFilter; label: string }> = [
  { value: "all", label: "All" },
  { value: "movie", label: "Movies" },
  { value: "series", label: "Series" },
  { value: "anime", label: "Anime" },
];

const SELECTED: Record<KindFilter, string> = {
  all: "border-border-strong bg-hover text-fg",
  movie: "border-kind-movie/40 bg-kind-movie/15 text-kind-movie",
  series: "border-kind-series/40 bg-kind-series/15 text-kind-series",
  anime: "border-kind-anime/40 bg-kind-anime/15 text-kind-anime",
};

export function KindTabs({
  value,
  onChange,
  label = "Filter by kind",
}: {
  value: KindFilter;
  onChange: (value: KindFilter) => void;
  label?: string;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className="flex gap-1 overflow-x-auto whitespace-nowrap pb-1 [scrollbar-width:none]"
    >
      {OPTIONS.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={selected}
            onClick={() => onChange(option.value)}
            className={[
              "min-h-control-sm shrink-0 rounded-control border px-3 text-small font-semibold transition-colors",
              selected
                ? SELECTED[option.value]
                : "border-border-subtle text-fg-muted hover:bg-hover hover:text-fg",
            ].join(" ")}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
