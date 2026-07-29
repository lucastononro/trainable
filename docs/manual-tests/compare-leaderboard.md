# Manual test tutorial — Comparison leaderboard (`/compare`)

Covers the comparison leaderboard shipped in PR #161 (issue #105): the
experiments-page selection flow and the `/compare` page (leaderboard table,
overlaid charts, session legend, feature overlap, cost totals).

The frontend has no unit-test framework (scripts are `lint` / `build` /
`format` only), so this page is verified manually. Static gates that must
pass first: `cd frontend && npx tsc --noEmit && npm run lint && npm run build`.

## Prerequisites

- Backend + frontend running (e.g. `docker compose up` or backend uvicorn +
  `cd frontend && npm run dev`).
- At least 3 experiments with sessions, at least 2 of which logged training
  metrics; ideally at least one with a prep summary (so `feature_overlap`
  is populated) and one still `created`/`failed` (no metrics).

## 1. Entry point — experiments page selection

1. Open `/experiments`.
2. Each row with a session shows a checkbox in the first column; rows
   without a session show none (only sessions can be compared).
3. Check one row → an amber "Compare selected (1/8)" button appears in the
   header, disabled with tooltip "Select at least 2 experiments to compare".
4. Check a second row → button enables. Click it.
5. Expect navigation to `/compare?sessions=<id1>,<id2>` — plus
   `&project=<id>` iff all selected sessions belong to the same project
   (verify the project name then shows in the compare header).
6. Selection cap: try to check a 9th session → it must not be added
   (counter stays at 8/8).

## 2. Leaderboard table

1. Header shows the trophy icon, "Comparison leaderboard", session count,
   and a Refresh button (spinner while loading).
2. One row per selected session; duplicate experiment names are
   disambiguated with a short session-id suffix like `name (a1b2c3)`.
3. One column per metric showing the latest (highest-step) value; the best
   value per metric column carries the trophy highlight — for loss-like
   metrics ("lower is better") the *smallest* value must win.
4. Default sort: ranked by the first (alphabetical) metric, best first.
5. Click a metric header → sorts by it; click again → direction flips
   (arrow icons update). Sort by Name, Cost, Created too.
6. Sessions without a value for the sorted column sink to the bottom in
   both directions.
7. Cost column formatting: `$0`, `<$0.01`, 3 decimals under $1, else 2.

## 3. Charts + legend

1. One chart per metric, all sessions overlaid with distinct palette
   colors; tooltip shows per-session values at a step.
2. Click a session in the legend → its line disappears from every chart
   and the legend entry dims/dashes; click again to restore.
3. If none of the sessions logged metrics: charts are replaced by "None of
   the selected sessions logged metrics yet." and no legend renders.

## 4. Feature overlap

1. With at least one session having a prep summary: a "Feature overlap"
   section renders — "Common to all (n)" green pills, then per-session
   rows listing only the extra (non-common) features with a color dot
   matching the chart palette.
2. With no prep summaries anywhere: the backend returns `feature_overlap:
   []` (empty list — this is the `never[]` union arm in
   `CompareResponse`); the section must be entirely absent, with no
   runtime error in the console.

## 5. Edge cases

1. `/compare` with no `sessions` param (or a single id): empty state with
   a link back to the experiments page — no fetch fired.
2. `/compare?sessions=<real>,<garbage-id>`: the unknown session appears as
   a `missing` row and is excluded from legend/charts; page still renders.
3. Kill the backend and hit Refresh: a rose error banner shows the
   message; restoring the backend + Refresh recovers.
4. Direct-load the URL (hard refresh): the Suspense fallback spinner shows
   briefly, then content — no hydration warnings in the console.
