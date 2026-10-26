export interface Ratings {
  imdb_rating?: number | null;
  rt_rating?: number | null;
  metacritic_rating?: number | null;
  sources?: string[];
}

const BADGE =
  "inline-flex items-center gap-1 rounded-full bg-hover px-2 py-0.5 text-[12px] font-semibold leading-5 text-fg";

function formatImdb(value: number): string {
  return value.toFixed(1);
}

export function RatingsRow({ ratings }: { ratings: Ratings }) {
  const hasImdb = ratings.imdb_rating !== null && ratings.imdb_rating !== undefined;
  const hasRt = ratings.rt_rating !== null && ratings.rt_rating !== undefined;
  const hasMetacritic =
    ratings.metacritic_rating !== null && ratings.metacritic_rating !== undefined;

  if (!hasImdb && !hasRt && !hasMetacritic) {
    return null;
  }

  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2" aria-label="Ratings">
        {hasImdb ? (
          <span className={BADGE} aria-label={`IMDb rating ${formatImdb(ratings.imdb_rating!)} out of 10`}>
            <span aria-hidden>★</span>
            <span>{formatImdb(ratings.imdb_rating!)}</span>
            <span className="text-[11px] font-medium text-fg-comment">IMDb</span>
          </span>
        ) : null}
        {hasRt ? (
          <span className={BADGE} aria-label={`Rotten Tomatoes ${ratings.rt_rating} percent`}>
            <span aria-hidden>🍅</span>
            <span>{ratings.rt_rating}%</span>
            <span className="text-[11px] font-medium text-fg-comment">RT</span>
          </span>
        ) : null}
        {hasMetacritic ? (
          <span className={BADGE} aria-label={`Metacritic ${ratings.metacritic_rating} out of 100`}>
            <span>{ratings.metacritic_rating}</span>
            <span className="text-[11px] font-medium text-fg-comment">Metacritic</span>
          </span>
        ) : null}
      </div>
      <p className="text-[11px] text-fg-comment">
        Ratings via IMDb, Rotten Tomatoes and Metacritic.
      </p>
    </div>
  );
}
