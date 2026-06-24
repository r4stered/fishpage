# Derive column boundaries from the header row, and flag rows that don't fit

The parser reconstructs the Stocklist's columns from pdfplumber word x-coordinates. The walking
skeleton did this with five hardcoded boundaries (`_SIZE_LEFT … _QTY_LEFT`) tuned to one PDF's
header. That is brittle in a way that fails silently: if a future Stocklist shifts a column even
slightly, a word lands in the wrong column with no error — corrupt data, not a crash. The most
insidious case is a long name word drifting into the `retail_price` column and being read as the
price. We now derive the boundaries from the header instead.

## Boundaries are the next column's header left edge

The six column headers (`Sku`, `SIZE`, `nm`, `retail_price`, `special_price`, `qty_avail`) are
read off the page, and each boundary is set to the **left edge of the next column's header label**.
A word belongs to the last column whose header starts at or before it; the SKU is the row's first
word, so only the five boundaries right of it are tracked.

Two simpler rules were tried against the sample Stocklist and rejected by the data:

- **Midpoint between adjacent header left edges** mis-columns the right-aligned numeric columns.
  `retail_price`, `special_price`, and `qty_avail` print their values flush-right, so a short price
  starts far to the right of its header's left edge — past the midpoint — and spills into the next
  column.
- **Midpoint of the whitespace gap between two header labels** mis-columns the name. The `nm` label
  is two characters wide while the names beneath it run ~150 points, so the gap midpoint sits well
  inside the name column and clips long names into `retail_price`.

The next-header-left-edge rule holds for both kinds of column because the supplier left-aligns every
column under its header: each column's leftmost token (a price's `$`, a name's first word) starts at
roughly its header's left edge, while the column to its left never reaches that far. The rule does
not assume a header label is as wide as its column, which is what broke the gap-midpoint rule.

The header row prints only on the **first page**; the remaining pages are data only. The layout is
therefore derived once, from whichever page carries the header, and reused for every page — the
columns are identical across pages, so there is nothing per-page to recompute.

The derived edges are required to read **strictly left to right**. The bands assume increasing
edges, so a header detected out of column order — a pdfplumber tokenisation quirk, say — would make
a band's lower bound exceed its upper bound and silently empty that column. A non-monotonic header
is rejected (`MissingHeaderError`) rather than used, turning an assumed invariant into a checked one.

## A misaligned price is flagged, not silently parsed

Dynamic boundaries shrink the chance of misassignment but cannot rule it out, so each row is
sanity-checked where corruption is most damaging: the price columns. A well-formed price column is
the `$` marker followed by exactly one amount (`["$", "12.99"]`). Any other shape — a bare number
with no marker, a stray token alongside the price — means a word from an adjacent column drifted in.
Rather than read the first number it finds and record a wrong price, the parser treats the row as
misaligned and skips it, logged by SKU through the same skip-and-summarize path the other
unparseable rows use. Silent misassignment becomes a visible signal.

The same shape check applies to `special_price`, the right-most and only optional price column, and
that is a deliberate tradeoff. A blank special column is normal and reads correctly as "no special
price". But a *garbled* special — present yet not `$ <amount>` — now drops the **whole row**,
including an otherwise-good retail price and quantity. Keeping the Item with `special = None` would
salvage more, but a malformed special is itself evidence the row's columns have shifted, and we would
rather lose one row visibly than record an Item whose other fields may be quietly wrong too. The
stricter rule wins for the same reason the rest of the check does: a misaligned row is surfaced, not
half-trusted.

## A Stocklist with no header fails loudly

Because positions come from the header, a Stocklist where no page carries one cannot be aligned at
all. The parser raises `MissingHeaderError` rather than falling back to the old literal coordinates:
a hidden fallback would re-introduce exactly the brittleness this change removes, and could mis-column
an unrecognised layout without anyone noticing. A truncated or unrecognised drop is surfaced, not
ingested. This composes with the watched-folder trigger, which already leaves a drop that "always
raises" in the incoming folder for retry rather than corrupting the catalog — see
[ADR 0005](0005-watched-folder-polling-trigger.md).

## Consequences

- Re-tuning to a relabelled Stocklist is a one-line change to the header-label list, not a hunt for
  five magic coordinates. A relayout that keeps the same labels needs no change at all.
- Reading columns by header position rather than fixed coordinates is the same word-geometry
  approach already relied on for the overloaded SIZE column — see
  [ADR 0002](0002-size-column-is-overloaded.md).
- The `$`-shape check assumes every genuine price carries the `$` marker, which holds for the sample
  Stocklist. A supplier change that dropped the marker would flag every priced row — loudly, as a
  visible signal, which is the intended failure direction.
- The header-detection and price-shape guards bound, but do not eliminate, the partial-extraction
  case left open by [ADR 0005](0005-watched-folder-polling-trigger.md): a drop that opens and yields
  a header still reconciles whatever rows it parsed.
