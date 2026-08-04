# ADR-0002 — Anomalib PatchCore as the default detector

**Status:** accepted · **Date:** 2026-08-04

## Context

Industrial visual inspection has very few labelled defects and many good samples. Defect
types are open-ended: a model trained on known defects fails on the next unseen one. The
production constraint is a high recall on defects at a tolerable false-alarm rate, with
localisation so an operator can see *where* the defect is.

## Options considered

1. **Supervised classifier (ResNet/EfficientNet)** — needs labelled defects in quantity,
   fails on unseen defect types. Rejected: does not match the data reality.
2. **Autoencoder reconstruction** — simple, but weak on subtle texture defects and
   notoriously sensitive to threshold choice.
3. **PatchCore (memory bank of nominal patch embeddings)** — trained on good samples only,
   strong MVTec AD results, produces pixel-level anomaly maps, no backprop training loop.
4. **Implementing PatchCore from the paper.**

## Decision

PatchCore via **Intel's Anomalib**, behind an `AnomalyDetector` port with a plugin registry.
PaDiM, FastFlow, Reverse Distillation and an autoencoder are registered alongside it and
selectable by config.

Explicitly **not** reimplementing PatchCore: the deliverable is the platform, and a
maintained library with published benchmarks is the professional choice over research code
this project would then own forever.

## Consequences

**Positive:** trains on nominal images only; pixel-level heatmaps for operators; fast to
"train" (feature extraction + coreset subsampling, no gradient descent); a well-tested
library carries the model correctness burden.

**Negative:** memory-bank size scales with training-set size and drives both RAM and
inference latency — coreset ratio becomes a tuned, monitored parameter. Anomalib's API has
broken between major versions, so the version is pinned and the adapter is the only place
that touches it. Anomalib constrains the Python version (see ADR-0007).
