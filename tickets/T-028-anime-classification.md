# T-028 — Anime classification

**Phase:** 7 · **Depends on:** T-003, T-004, T-005 · **Estimate:** M

## Goal

Stop making the user choose the **Anime** tab before Daku admits a title is anime. A search for
*Cyberpunk: Edgerunners* on the IMDb path currently produces a `series` candidate; adding it
stores a series row with no AniList identity, no absolute episode numbers, and TVDB-style
episode data. Nyaa (T-027) and anime filenames then have nothing reliable to match against.

Classification must be **explainable, conservative and overrideable**. A wrong guess is worse
than "series": it changes episode numbering, provider selection and filing.

## Scope

### Signals

New `services/metadata/classification.py`:

```python
@dataclass
class AnimeSignal:
    is_anime: bool
    confidence: Literal["mapped", "exact", "none"]
    anilist_id: int | None
    reason: str
```

Resolution order:

1. **AniBridge mapping** — look up `anime_mappings` for `src_provider="imdb"`,
   `src_id=tt...` → AniList id. `confidence="mapped"`, no network. This is the only signal that
   may auto-convert an existing `series` candidate into `anime`.
2. **AniList exact match** — only when the user explicitly searched the Anime tab or the title
   is already `kind=anime`: query AniList by title, require a normalised exact title (or one
   synonym) and `year ± 1`, then require `format` in `TV|MOVIE|OVA|ONA|SPECIAL|TV_SHORT` and
   `countryOfOrigin == "JP"`. `confidence="exact"`. This may *offer* a conversion, never silently
   convert a library row.
3. **None** — do not guess from genre or “Animation” alone. Western animation (`Arcane`,
   `Invincible`), Japanese live action and anime-styled games stay series/movie.

Mapping data is already ingested by T-005; do not add a second mapping format. If the dataset
has not been ingested, classification returns `confidence="none"` and the doctor already says
`mapping: degraded`.

### Search

- `TitleCandidate` gains `anime: bool = False`, `anilist_id: int | None`, and
  `classification_reason: str | None`. Keep `to_dict` additive.
- When `kind` is `None` or `series`, enrich IMDb/Cinemeta candidates with the mapping lookup in
  one batch (one DB query per search, not per card). For a mapped candidate:
  - keep the IMDb candidate visible, set `anime=true`, `anilist_id=<id>`, and show an **ANIME**
    badge on the card;
  - Add uses the AniList identity, not the series path (see below).
- When `kind="anime"`, merge AniList results with mapped IMDb candidates, dedupe by
  `anilist_id`. AniList remains the primary list; mapped IMDb rows only fill gaps.
- Preserve the current behaviour when the mapping is unavailable: the Anime tab still returns
  AniList results, and the IMDb path still works.

### Add and re-hydration

- `POST /api/titles` accepts a candidate carrying `source=imdb`, `kind=series`,
  `anime=true`, `anilist_id`. Resolution:
  1. fetch AniList detail by `anilist_id` (T-004 path);
  2. keep the IMDb id on the row; set `kind="anime"`, `metadata_source="anilist"`;
  3. build episodes from AniList/TVDB via the existing mapping path, preserving absolute
     numbers;
  4. if AniList is unreachable, do **not** create a half-anime row: return
     `METADATA_UNAVAILABLE` and let the user retry, or fall back to series only with an
     explicit `anime=false` request from the UI.
- If the title already exists as `series` with the same IMDb id, do not silently change its
  kind. Return `{existing: true, classification: {...}}` and let T-029's fixer convert it with
  the user's confirmation.
- `GET /api/titles/{id}` and the preview include a `classification` block:
  `{is_anime, confidence, reason, suggested_anilist_id}` so the UI can explain the badge.

### UI

- Search cards and preview show the existing kind badge plus the `ANIME` badge when
  `anime=true`; the badge tooltip reads `Detected via AniBridge mapping` or
  `AniList exact match`.
- The preview's Add button says **Add as anime** when the signal is mapped/exact, and the
  resulting title page is the anime path.
- If a candidate is already in the library as `series` but now classifies as anime, show an
  inline notice: `This looks like anime. Fix the classification?` linking to the T-029 dialog.
  No automatic migration.
- Do not show a classification notice on movie searches unless a mapping exists.

## Out of scope

Bulk reclassification of the existing library (T-029 is per-title; a later ticket can batch).
Changing episode numbering on already-downloaded media. AniList for non-anime. Detecting anime
from filenames alone. Hentai/adult categories.

## Acceptance criteria

- [x] `Cyberpunk: Edgerunners` (`tt12590266`) appears as anime in an unfiltered search when the
      mapping dataset contains its IMDb → AniList pair, and Add stores `kind="anime"` with both
      `imdb_id` and `anilist_id`.
- [x] A mapped anime added from the Anime tab uses AniList episodes and absolute numbers, not a
      synthetic season-1 list.
- [x] `Arcane`, `Invincible`, and a Japanese live-action series are not classified as anime.
- [x] Genre `Animation` alone never triggers classification.
- [x] A stale/missing mapping degrades to `confidence="none"` without a 500 and without
      blocking search.
- [x] An existing series row is never mutated automatically; the notice and T-029 link appear.
- [x] Classification adds at most one mapping query per search, not one per card.
- [x] The reason string is user-readable and contains no internal SQL or dataset ids.
- [x] Tests use recorded mappings and fixtures; no live AniList or IMDb calls in CI.

## Test plan

- `tests/metadata/test_classification.py`: mapped hit, exact-match hit, year mismatch, country
  mismatch, western animation, missing dataset, duplicate synonym.
- `tests/routers/test_search.py`: enrichment in `kind=None`, anime merge/dedupe, one DB query.
- `tests/routers/test_add_title.py`: mapped anime add, AniList failure, existing series conflict.
- `web/src/pages/__tests__/search.test.tsx`: ANIME badge, tooltip/reason, Add as anime.
- `web/src/pages/__tests__/preview.test.tsx`: existing-series notice and fixer link.

## Implementation notes

- Look up the mapping through a repository helper that takes a list of IMDb ids and returns a
  dict, so search is one query.
- Do not call AniList for every IMDb candidate; the exact-match fallback is only for the
  explicit Anime tab or an already-anime title.
- Keep `TitleCandidate.kind` as the truth for routing. `anime=True` is an identity hint, not a
  fourth kind; after Add the row's `kind` is `anime`.
- The mapping dataset may use `imdb` or `imdb_id` provider names; normalise once in the
  repository, not in every caller.
- Log the classification decision as `title classified` with `confidence` and `reason`, never
  the whole candidate payload.

## Completion notes

- The shipped AniBridge v3 dataset exposes only `imdb_movie` rows (about 2,743 IMDb ids) and
  has no `imdb_show` coverage, so several TV anime are absent from the mapping even though
  AniList knows them: Frieren (`tt22248376`) and Edgerunners (`tt12590266`) miss the mapping,
  while Mushoku Tensei (`tt13293588`) is present.
- To keep unmapped anime discoverable, a `kind=None|series` search also runs **one** batched
  AniList exact-match fallback after the mapping lookup (normalised exact title or synonym,
  year ±1, `countryOfOrigin == "JP"`, animation format). Mapping hits stay
  `confidence="mapped"`; fallback hits are `confidence="exact"`.
- The fallback is guarded by `mapping_dataset_present`; a missing dataset still degrades to
  `confidence="none"` without a network call and without blocking search.
- Existing series rows are never mutated. Frieren, Edgerunners and Mushoku Tensei in the
  current library must be converted once through T-029's **Fix metadata** action; the title
  page and preview show the `This looks like anime` notice when the refreshed classification
  finds an AniList identity.
