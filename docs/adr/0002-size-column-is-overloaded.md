# The SIZE column is an overloaded grade/unit; store it raw, interpret per-category later

The Stocklist's `SIZE` column looks like a clean enum of size grades (`-`, `S`, `M`, `L`, `Jumbo`),
and the obvious design is to parse it into exactly those five values. We deliberately don't. Reading
the column by word coordinates across the sample Stocklist shows it is overloaded: alongside the
~865 livestock rows carrying real grades, ~100 plant and dry-goods rows put a **packaging unit** in
the same column — `w/weight` (39), `POTTED` (19), `XL` (8), `ON MAT` (6), `BUNCH` (4),
`12 PC CASE` (2), `Half Bag`, `POSTER`, `1/2 SQ. FT.` — plus 22 rows where the cell is blank.

A size grade describes a fish; a packaging unit describes a bunch of plants or a case of dry goods.
Whether a given token is a grade or a unit therefore depends on the Item's Derived Category. Rather
than coerce (and lose) the non-grade tokens, or pretend `size` is a five-value enum it demonstrably is
not, the parser stores the **raw column token verbatim** (a blank cell becomes `-`). Derived Category
is now computed, but applying it to the `size` token — splitting grade from unit — is still left to a
later slice.

## Consequences

- `size` is free-form supplier text in v1, not a validated enum. A reader will see values like `POTTED`
  in the field; that is by design, not a parse bug.
- The future Size **filter** (issue #1, story 14) cannot treat `size` as a clean grade set until the
  category-aware interpretation lands. It must either filter on the raw token or wait for that slice.
- No data is discarded, so the later interpretation step has the original tokens to work from — consistent
  with ADR-0001's principle of never throwing away supplier signal we might want later.
