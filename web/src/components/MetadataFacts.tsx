export interface MetadataFactsData {
  runtime_minutes?: number | null;
  genres?: string[];
  cast?: string[];
  directors?: string[];
  writers?: string[];
  studios?: string[];
}

const CHIP =
  "inline-flex items-center rounded-full bg-hover px-2 py-0.5 text-[11px] font-medium leading-4 text-fg-muted";

function Chips({ values }: { values: string[] }) {
  return (
    <div className="flex flex-wrap gap-1">
      {values.map((value) => (
        <span key={value} className={CHIP}>
          {value}
        </span>
      ))}
    </div>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
      <dt className="shrink-0 text-[12px] text-fg-comment">{label}</dt>
      <dd className="min-w-0 text-[12px] text-fg-muted">{children}</dd>
    </div>
  );
}

export function MetadataFacts({ facts }: { facts: MetadataFactsData }) {
  const genres = facts.genres ?? [];
  const cast = facts.cast ?? [];
  const directors = facts.directors ?? [];
  const writers = facts.writers ?? [];
  const studios = facts.studios ?? [];
  const runtime = facts.runtime_minutes ?? null;

  const hasAnything =
    runtime !== null ||
    genres.length > 0 ||
    cast.length > 0 ||
    directors.length > 0 ||
    writers.length > 0 ||
    studios.length > 0;

  if (!hasAnything) {
    return null;
  }

  const shownCast = cast.slice(0, 5);
  const extraCast = cast.length - shownCast.length;

  return (
    <dl className="flex flex-col gap-2 text-[12px]">
      {genres.length > 0 ? (
        <div className="flex flex-wrap items-baseline gap-x-2">
          <dt className="shrink-0 text-[12px] text-fg-comment">Genres</dt>
          <dd>
            <Chips values={genres} />
          </dd>
        </div>
      ) : null}
      {runtime !== null ? <Fact label="Runtime">{runtime} min</Fact> : null}
      {studios.length > 0 ? <Fact label="Studio">{studios.join(", ")}</Fact> : null}
      {directors.length > 0 ? <Fact label="Director">{directors.join(", ")}</Fact> : null}
      {writers.length > 0 ? <Fact label="Writer">{writers.join(", ")}</Fact> : null}
      {shownCast.length > 0 ? (
        <Fact label="Cast">
          <span>{shownCast.join(", ")}</span>
          {extraCast > 0 ? (
            <span
              className="ml-1 text-fg-comment"
              title={cast.slice(shownCast.length).join(", ")}
            >
              +{extraCast}
            </span>
          ) : null}
        </Fact>
      ) : null}
    </dl>
  );
}
