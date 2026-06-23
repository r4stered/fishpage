# Fuzzy name search: filter by per-token partial ratio, then rank survivors by whole-name WRatio

Name search has to be approximate and order-independent — the motivating case is `"angel koi"`
finding `"Angelfish Koi"`, where each query word only partially spells a name word and the order
need not match. The obvious design is one fuzzy score per name (some whole-string ratio against the
whole query) with a cutoff. We deliberately don't: scored whole-string, `"angel koi"` against
`"Angelfish Koi"` lands around 75 — below any cutoff that also keeps out genuine non-matches — so a
single ratio cannot both find the target and reject the noise.

We pull in **rapidfuzz** (fast, MIT, no transitive dependencies) and split the two jobs across two
scorers, because the scorer that filters well ranks badly and vice versa:

- **Filter — `partial_ratio`, per token, cutoff 80.** Each whitespace token of the query must match
  some word of the name at `partial_ratio >= 80`. A short token that prefixes a longer word
  (`"angel"` in `"Angelfish"`) scores 100, so partial spellings match while unrelated words fall well
  below. Every query token must land, so `"barb koi"` does not match `"Barb Cherry"`.
- **Rank — `token_sort_ratio`, whole name.** `partial_ratio` saturates at 100 for any substring hit,
  so it cannot tell an exact match from a loose one and is useless for ordering. The survivors are
  sorted by `token_sort_ratio` of the whole query against the whole name (tokens sorted first, so word
  order doesn't matter): `"angel koi"` scores `"Angel Koi"` at 100, the clean `"Angelfish Koi"` at ~82,
  and the extra-word `"Angelfish Koi Smokey"` lower still. A substring-based composite such as `WRatio`
  was rejected for ranking: it rewards containment, so padding a name with extra words *raises* its
  score and floats looser matches above the tighter one a searcher typed for. Ties keep input order
  (the sort is stable).

A blank term is no search at all and passes every Item through, unranked.

## Consequences

- A new runtime dependency, `rapidfuzz`. It is the only non-framework runtime dependency added beyond
  the parse/serve stack.
- The cutoff (80) and the two-scorer split are tuned against the sample Stocklist's naming. They are
  heuristics, not guarantees — markedly noisier names may need the cutoff or scorers revisited.
- Search runs in process over the already-loaded Items, alongside the in-stock and Derived Category
  filters, rather than in SQL. That is fine at the current catalog size (~1k Items); a much larger
  catalog would argue for pushing the match into the query layer.
- Results are relevance-ranked, so search reorders the grid. The other filters (stock, category)
  preserve order; search is the one control that does not.
