# Web UI — deferred cleanup

Low-priority findings from the 2026-06-05 adversarial review of the Howgozit +
iPad-tap work. The medium-priority items (silent `patchCell` save failures; three
overlapping row-dialog flush mechanisms) were fixed in that pass. The items below
are cleanup/consistency, not correctness-critical — safe to defer.

## Bloat / churn residue

- **L1 — Duplicated field-parsing.** `parseRowAddFieldPanel` (`static/howgozit.js`)
  and `parseNewColumnForm` are ~80% identical (type/unit/step/options/uppercase).
  Extract a shared `parseFieldDefinition(root)` so fixes apply to both.
- **L3 — Dead variable `mounted`** (`static/howgozit.js`): assigned in `mount()`,
  never read. Remove, or use it to assert mount before API calls.
- **L7 — Orphaned CSS** `.instList` / `.instList li` / `.instIcon`
  (`static/app.css`): referenced in no JS/HTML (grep-confirmed). Confirm the
  instruments view markup, then delete.

## Improperly-applied-patch leftovers

- **L2 — Vestigial `*-wired` guards.** `wireCompassSettings` / `wireAirspeedSettings`
  (`static/app.js`) open with a `dataset.compassWired/airspeedWired === '1'` guard,
  but `renderAttrs` strips that attribute immediately before calling them, so the
  guard never fires. Either delete the guard lines or stop stripping the attribute —
  given the `innerHTML`-replace render model, deleting the guard is correct.
- **L5 — `rowid` equality inconsistency.** `patchCell` uses raw `r.rowid === rowid`
  while every other lookup uses `Number(r.rowid) === rid`. Works today (JSON ids are
  numbers); brittle if the API ever returns a string id. Normalize to one form.

## Consistency / hygiene

- **L4 — `.clockWarn` orphan class.** Used in `static/app.js` to highlight clock
  cause / stale-GPS warnings, but no CSS rule exists. Add
  `.clockWarn { color: var(--warn); font-weight: 700; }`. (May predate the 2026-06-05
  batch — verify.)
- **L6 — Two `escapeHtml` implementations.** `app.js` `escapeHtml` escapes only
  `& < >` (relies on a separate `escapeAttr` for attribute contexts); `howgozit.js`
  `escapeHtml` also escapes `"`. Both individually safe; consolidate to one shared
  util to avoid a future mismatch.
- **L8 — CSS hygiene (optional).** Tap-target magic numbers `44px`/`52px` (~15×) →
  `--tap-target` / `--btn-height` vars; hardcoded accents `#ff7ce0`, `#051125` →
  vars; near-duplicate `.hgz-row-*` dialog-button rules → a `.hgz-btn` base; 7×
  deprecated `-webkit-overflow-scrolling: touch` (no-op on modern iOS) droppable.

## Surfaced by the new JS tests (internal/web/jstest)

- **`parseTimeToTsNs` empty-part quirk** (`static/howgozit.js`): an empty HH or MM
  part coerces to 0 via `Number('')`, so `':'` parses to 00:00 and `'21:'` to
  21:00 instead of being rejected as incomplete. Currently a debounced edit of a
  half-typed time can silently persist `*:00`. Consider rejecting empty parts
  (then M1's cell-error surfaces it). Behavior is locked by a test, so tightening
  is a deliberate change. Low priority.

## Related (surfaced while fixing M1)

- `addRow` and `deleteRow` (`static/howgozit.js`) still fail silently via
  `console.error` (no pilot-visible feedback), unlike cell edits now. `createLog`
  uses `alert()`. Consider a single small toast/error surface for all
  howgozit mutations so the UX is uniform.
