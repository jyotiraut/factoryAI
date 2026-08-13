# ADR-0015 — Closing the loop: feedback folds into images, drift triggers retraining

**Status:** accepted · **Date:** 2026-08-13

## Context

Every piece this phase needed already existed, built by earlier phases for other reasons:
`POST /feedback` and `SubmitFeedback` (Phase 7) already recorded an operator's verdict as an
immutable `Feedback` row; `InspectionImage` (Phase 1) already had a `relabel()` method and a
transition table; `CreateDatasetVersion` (Phase 4) already selected `list_trainable()` images
into a deterministic split; `GenerateDriftReport` (Phase 11) already computed
`should_trigger_retraining`; `retraining_dag` (Phase 10) already ran the full version → train
→ evaluate → deploy pipeline end to end, just never on its own. What Phase 11 explicitly
deferred (its ADR-0014, "Consequences") was the wiring between them: feedback was recorded
but went nowhere near the image it was about, drift was measured but never acted on. This
phase is entirely that wiring — no new entity, no new port, no new DAG.

## Decisions

**A reviewed prediction's image goes straight from `PENDING` to `VALID` — a new, deliberate
transition, not a bug.** A production inference image never runs through `IngestImage`'s
validation chain (Phase 7's own design: an inference request must always be scored, never
rejected), so it sits at `PENDING` indefinitely once served. An operator reviewing that
image's prediction through `POST /feedback` is a *stronger* qualification signal than the
automated chain, not a weaker one — a human already looked at it. `InspectionImage.
_ALLOWED_TRANSITIONS[PENDING]` now includes `VALID` for exactly this path; the pre-existing
`test_pending_cannot_jump_to_valid` test asserted the old, now-obsolete premise and was
rewritten (`test_pending_can_jump_to_valid_via_operator_feedback`) alongside a new
`test_archived_is_terminal` to keep the transition table's terminal-state guarantees covered.

**`SubmitFeedback` folds ground truth into the image in the same transaction as the feedback
record, not a separate step.** `execute()` now fetches `uow.images.get(prediction.image_id)`
and calls a new `_fold_ground_truth_into_image()` helper: `image.mark_valid()` (suppressed if
the image is already terminal — `REJECTED`/`ARCHIVED` stays out of the trainable set
regardless of feedback, since those statuses already decided this image should never train a
model) then `.relabel(ground_truth).with_metadata(feedback_reviewed=True)`. No separate
"promote reviewed images" batch job exists or is needed — reaching the next dataset version is
this use case's job, immediately, not a later phase's.

**A metadata flag (`feedback_reviewed`), not a new column or a new entity.** Nothing else
needs to query on this fact — only `CreateDatasetVersion` reads it, and only to decide a
split assignment — so a speculative dedicated column would be exactly the kind of schema
`docs/CONTRIBUTING.md` already warns against adding before something has actually queried
it. `InspectionImage.metadata` already existed for free-form provenance; this is one more key
in it.

**Feedback-reviewed images are pulled out of the ratio-based shuffle and placed in `TEST`
unconditionally — a growing regression suite, not a bigger training set.** `_assign_splits()`
now partitions images into `reviewed`/`unreviewed` before doing anything else: `unreviewed`
still goes through the original sorted-then-seeded-shuffle logic Phase 4 built, `reviewed`
is sorted deterministically and appended to `TEST` regardless of the caller's `split_ratios` —
demonstrated directly by a test that requests `train=1.0, val=0.0, test=0.0` and still finds
the reviewed image in `TEST`. Every prediction an operator has ever corrected is something
every future model version is evaluated against, forever after, not just the training run it
happened to arrive in time for — that's what makes it a *regression* suite: a model that
regresses on a case a human already fixed once should fail evaluation, not silently pass
because that image also got shuffled into `TRAIN`.

**`monitoring_dag` triggers `retraining_dag` directly via `airflow.api.common.trigger_dag`,
not a `TriggerDagRunOperator`.** The task needs to inspect `check_drift`'s own XCom return
value (`should_trigger_retraining`, `severity`) to decide whether to trigger at all — a plain
`TriggerDagRunOperator` has no native conditional-skip based on an upstream task's result
without templating around it. A `@task` that raises `AirflowSkipException` when the report
doesn't warrant it, and calls `trigger_dag(dag_id="retraining", conf={...})` when it does,
stays in the same TaskFlow style every other task in this codebase already uses, and keeps
the decision (`should_trigger_retraining`) exactly where ADR-0014 already put it — on the
domain entity, not re-derived in Airflow. The triggered run's `conf` only overrides
`category` and `reason`; `retraining_dag`'s own declared `params` (`dataset_name`,
`model_name`, `seed`) still apply as defaults, the same merge behaviour any manually
triggered run already relies on.

## Consequences

- The full loop is now real: `POST /feedback` → image relabelled and marked `VALID` →
  next `CreateDatasetVersion` run includes it, pinned to `TEST` → next drift check that
  breaches its threshold starts `retraining_dag` unattended → version → train → evaluate →
  deploy-or-reject (Phase 6's gate, unchanged) — matching this phase's exit criteria
  ("a simulated drift event produces either a deployed better model or a documented
  rejection, with no human intervention").
- `monitoring_dag`'s own docstring claim from Phase 11 ("this DAG only measures and
  records") is now false and has been rewritten — it still doesn't alert (that's still
  Prometheus/Alertmanager's job, unchanged from ADR-0014), but it does act on its own
  measurement.
- No frontend or dedicated feedback-review UI ships here — `POST /feedback` was already a
  complete API surface (Phase 7); Phase 13 owns the UI that calls it.
- Three pre-existing tests broke on contact with this phase's changes, all for the same
  reason (a test built before `SubmitFeedback` touched the image repository at all): two
  `SubmitFeedback` tests and one API-level feedback test didn't seed an `InspectionImage`
  matching the prediction's `image_id`. Fixed by seeding one in each, plus new assertions
  confirming the image ends up relabelled, `VALID`, and flagged — not just leaving the
  original assertions in place.

## Live verification

Live-verifying this phase meant actually triggering `retraining_dag` and watching it run —
not just confirming `trigger_dag()` queues a run, which Airflow will happily do against a
pipeline that then fails on its first real task. It did, four times over, each failure real
and each one previously invisible to every unit test in this codebase because none of them
exercise `factoryai.pipeline_runner` inside Airflow's actual isolated venv:

1. **`git` was never installed in the Airflow image at all** — `apt-get install` only ever
   listed `python3-venv`. `DvcGitVersionControl.current_commit()` failed with a bare
   `PermissionError` the moment `version_dataset` ran for real. Fixed by adding `git` as its
   own late Dockerfile layer (after the ML dependency install, so adding it doesn't force
   pip to redo that expensive install every time this layer changes).
2. **`bootstrap.container._REPO_ROOT` assumed an editable install.** `Path(__file__).
   resolve().parents[3]` is correct for every place this ran until now — the host's own
   `.venv`, an editable install inside the actual checkout — but inside `/opt/factoryai-venv`
   (a regular, non-editable install) it resolved to a `site-packages` path with no `.git`
   anywhere near it. `FACTORYAI_REPO_ROOT` now overrides it; ADR text above covers why.
3. **`dvc`, a pip-installed console script, isn't on `PATH` inside the isolated venv** —
   unlike `git`, an apt package already on the system `PATH`. `subprocess.run(["dvc", ...])`
   raised `PermissionError` searching `PATH` and hitting an unrelated non-executable `dvc`
   entry before reaching `/opt/factoryai-venv/bin/dvc`. `DvcGitVersionControl._run` now
   resolves the executable against `sys.executable`'s own directory first.
4. **`.dvc/config`'s `endpointurl = http://localhost:9000` is correct on the host** (where
   the API/worker actually run, ADR-0013) **and wrong inside any container**, where
   `minio:9000` is the reachable address. The first fix attempted was `dvc remote modify
   --local` run directly against the bind-mounted host repo — which, because a bind mount is
   the *same* file, silently rewrote the host's own `.dvc/config.local` to point at an
   address that doesn't resolve outside Docker's network, breaking `dvc` for the host session
   that had been driving this whole investigation. Caught immediately (a bare `git
   rev-parse`/`dvc push` from the host would have failed clearly, but wasn't yet retested
   when live-verification continued smoothly) and reverted. The real fix: Airflow's own
   containers get a `factoryai-repo-clone` *named volume*, recloned fresh from a read-only
   host mount by `airflow-init` on every stack start, and the `dvc remote modify --local`
   override lands in that clone's own `.dvc/config.local` — never the host's.
5. **`opencv-python` (an `anomalib` transitive dependency) needs `libGL.so.1`** — absent from
   every apt install in the image, so the moment `train` actually imported the detector
   factory, `cv2`'s compiled extension failed to load. Fixed by adding `libgl1`/
   `libglib2.0-0` alongside `git`.

With all five fixed, `version_dataset` and `train` were confirmed to actually run to
completion inside Airflow, not merely inside a unit test's fakes: a real `dvc push` against
MinIO succeeded (`dvc_hash` returned, image_count 284), a real `retraining` DAG run
progressed past `version_dataset` (Airflow's own task state, not a manual CLI call), and a
real PatchCore training run completed 11,681 coreset-selection iterations end to end on this
host's CPU (~31 minutes) with no crash — proof the isolated venv is now capable of the full
pipeline, independent of how long any one training run happens to take.

Two things this pass did **not** wait on, deliberately: `evaluate`/`deploy` completing for
this specific triggered run (CPU-only PatchCore training runs on the order of tens of
minutes per attempt; the previously-broken, Phase-12-relevant surface — orchestration
actually reaching and completing `version_dataset`/`train` inside Airflow — was what needed
proving, not Phase 6's already-tested promotion gate), and a real end-to-end drift-severity
breach (Phase 11's own live verification already exercised `GenerateDriftReport`; this pass
verified `trigger_dag()` itself genuinely queues and runs a `retraining` DAG run, which is
the new part).
