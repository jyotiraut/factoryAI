# ADR-0016 — Frontend dashboard: a real read API before a single component

**Status:** accepted · **Date:** 2026-08-13

## Context

The ROADMAP's Phase 13 exit criterion — "all dashboard data comes from the public API, no
backdoor queries" — was a constraint on *starting order*, not just a check to run at the
end. Auditing the existing API surface (Phases 7–12) before writing any UI found that eight
of the ten listed dashboard views had no supporting endpoint at all: predictions could be
submitted and individually fetched by id, but never listed; dataset versions, training runs
and drift reports could only be read one dataset/model at a time, never browsed; deployment
history existed but had no HTTP route; nothing distinguished a reviewed prediction from an
unreviewed one. Building the frontend against what existed would have meant either
backdoor-querying the database from React (violating the exit criterion directly) or
shipping empty views — so this phase is two phases in one commit: a real read-only backend
surface, then the frontend that consumes only it.

## Decisions

**A single generic `Page[T]` pagination envelope (`application/pagination.py`,
mirrored as a Pydantic model in `api/schemas.py`), not a bespoke shape per list
endpoint.** Every prior use case that returned more than one row either had a fixed count
(`ListProductionModels`, one per requested category) or a hard `limit` with no "how many
more" (`list_deployments`). The dashboard's list views are the first callers that need a
genuine "page 2 of N", so one `{items, total, limit, offset}` shape, reused across
predictions, drift reports, dataset versions and training runs, rather than four
near-identical dataclasses.

**Five new specific repository methods, not a generic filter interface.**
`repositories.py`'s own module docstring already commits to this ("query methods are kept
deliberately specific... a generic query interface pushes the query logic into the caller"):
`PredictionRepository.list_recent`/`list_needing_feedback`, `DriftReportRepository.
list_recent`, `DatasetRepository.list_all_versions`, `ExperimentRepository.list_recent`.
Each does one named query with a total count computed in the same round trip
(`select(func.count())`), not `len(list_x(..., limit=huge))`.

**Six new `VIEWER`-level permissions** (`VIEW_PREDICTIONS`, `VIEW_DRIFT`, `VIEW_DATASETS`,
`VIEW_TRAINING_RUNS`, `VIEW_DEPLOYMENTS`, `VIEW_SYSTEM_HEALTH`), all at the same rank as the
existing `VIEW_MODELS`/`VIEW_JOBS`. A dashboard is, by construction, read access for anyone
with an account — no view in the ROADMAP's list needed a narrower audience than "logged
in", so none of the six invents a new rank.

**`GET /auth/me`, not client-side JWT decoding, for role-aware navigation.** The access
token's own `role` claim is right there and free to read — but `api/dependencies.py`'s
`get_current_user` already deliberately re-reads the role from the database on every
request rather than trusting that claim, specifically so a role change or deactivation
takes effect on the very next request. A nav bar that decoded the stale claim instead would
reintroduce exactly the staleness that dependency exists to avoid, just for a different
caller. One more authenticated `GET`, reusing `UserResponse`, costs nothing and keeps the
freshness guarantee uniform.

**Defect trend is a Python-side bucketing of `list_in_window`, not a new SQL aggregation.**
`GetDefectTrend` reuses Phase 11's `PredictionRepository.list_in_window` (already the
reference-window query drift detection reads) and groups up to 5,000 predictions by day in
memory. A category with enough daily production traffic to make that grouping itself the
bottleneck needs a real `GROUP BY day` query — tracked, not silently accepted; the constant
guarding it (`_MAX_SAMPLE`) is named and documented specifically so it is easy to find when
that day comes.

**Live updates are polling (`refetchInterval`), not WebSockets/SSE.** The ROADMAP's own
scope line says "websocket/SSE live updates"; grepping the entire backend for either turned
up nothing — no infrastructure to build the frontend against exists yet, and building a
general-purpose push channel from scratch was not this phase's job to invent unprompted.
React Query's `refetchInterval` (15s on Live Inspection and System Health, the two views
where staleness is most visible) gets the same *user-visible* effect — a screen that updates
itself — without a new server-side component. A future phase that has a concrete reason to
need sub-second latency (not just "the ticket says SSE") can build it against a real
requirement.

**Feedback is submitted via one-click preset buttons, not a form.** The exit criterion is
"an operator can review a prediction and submit feedback in under three interactions" — a
verdict dropdown plus a corrected-label dropdown plus a submit button is three on its own,
before typing anything. `FeedbackQueuePage`/`LiveInspectionPage` instead offer "Confirm
correct" and "Mark incorrect" (which infers the corrected label from the existing verdict —
if the model said "defect", correcting it can only mean "good", and vice versa) as two
single-click actions. Free-text notes and a bounding-box region are cut from this UI
entirely — `POST /feedback` still accepts them, so a future, richer review UI can add that
input without a backend change.

**No image or heatmap rendering in the dashboard yet.** `PredictionHistoryResponse` carries
no image URL — `Prediction`'s own persisted fields are a `StorageLocation` for the heatmap
(present only briefly after a live `/predict` call, per that response's own docstring) and
an `image_id`, not a stable, presigned, cacheable URL a browser can load directly. Building
that (presigned-URL-by-id, an access-controlled proxy, or similar) is real, scoped-out work,
not an oversight — "Live Inspection" as shipped shows every number a reviewer needs (score,
threshold, confidence, verdict) to make the same correct/incorrect call, just not the
picture itself.

**A pure client-side auth flow: localStorage tokens, no httpOnly cookies.** The backend's
own token design (Phase 8) is a bearer access/refresh pair meant to be read and attached by
the client, not a cookie-based session — matching that contract on the frontend, rather than
introducing a cookie flow the backend was never built to issue, is the smaller change.
`api/client.ts` retries a 401 exactly once behind a single in-flight refresh promise (so N
requests racing the same expiry do not each spend the backend's single-use refresh-token
rotation), then forces re-login on a second failure.

## Consequences

- `GET /predictions`, `GET /predictions/feedback-queue`, `GET /drift/reports`, `GET
  /datasets/versions`, `GET /training/runs`, `GET /models/versions`, `GET
  /models/deployments`, `GET /analytics/defect-trend`, `GET /system/health` and `GET
  /auth/me` are all real, tested, permission-gated endpoints now — nine new use cases, five
  new repository methods (implemented in both the real SQLAlchemy repositories and the
  in-memory fakes), 50 new backend tests, zero regressions against the existing 499.
- `frontend/` is a separate Vite + React 19 + TypeScript project, not folded into the
  Python package — it has its own `package.json`, `.env`/`.env.example`
  (`VITE_API_BASE_URL`), and dev server on port 3000 (matching `API_CORS_ORIGINS`'s
  existing default, so CORS needs no reconfiguration for local dev).
- The dashboard's ten views (Live Inspection, Prediction History, Feedback Queue, Defect
  Trends, Models, Deployments, Dataset Versions, Training Runs, Drift Status, System
  Health) all ship, each backed by a real endpoint above — nothing in the frontend queries
  anything but `fetch()` against this API.
- Not built: a presigned-image/heatmap viewer (see "Decisions"), WebSocket/SSE push (see
  "Decisions"), and a user-management UI (`POST /auth/register` is administrator-only and
  already existed pre-Phase-13; no view in the ROADMAP's list asked for one).

## Live verification

`tsc -b`, `vite build` and `oxlint` all pass against the full frontend with zero errors (two
harmless `react/only-export-components` fast-refresh warnings in `AuthContext.tsx`, from
colocating the hook with its provider — accepted, not fixed, since splitting them into
separate files for a lint warning with no functional effect is not worth the indirection).
`app.openapi()` was inspected directly to confirm all ten new routes are actually registered
on the running `FastAPI` app, not just present in source. The Vite dev server was started
and confirmed to serve the app shell correctly on `:3000`.

Two verification gaps, both environment limitations rather than code defects, disclosed
rather than glossed over:

1. **No real, browser-rendered check of the running UI.** This session has no browser
   automation tool available — `WebFetch` reads static HTML through a text-extraction
   model, which cannot execute the client-rendered React app or click anything. Every claim
   above about the UI's *correctness* rests on `tsc`'s type-checking, a clean production
   build, and reading the code — not on having clicked through it. A real browser check
   (interacting with the login form, the feedback buttons, the charts) is recommended before
   treating this phase as fully done, and is flagged here so it is not silently skipped.
2. **The host-based backend could not reach Postgres in this sandbox** — the identical
   "password authentication failed despite correct credentials" artifact ADR-0014 already
   documented and diagnosed during Phase 11's live verification (host-loopback-to-Docker-
   forwarded-port connections fail intermittently in this specific sandboxed environment;
   container-to-container connections do not). This blocked an end-to-end login-through-
   real-Postgres check from the host. Not re-investigated further, since it is already a
   known, previously-diagnosed environment quirk, not a new one — see ADR-0014 for the full
   account.
