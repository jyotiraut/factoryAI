# ADR-0003 — MinIO locally behind a cloud-agnostic port

**Status:** accepted · **Date:** 2026-08-04

## Context

Inspection images, heatmaps and model artifacts are binary blobs that will reach hundreds
of gigabytes. They must not live in PostgreSQL, and the local development experience must
match production behaviour closely enough that S3 semantics (eventual listing, presigned
URLs, multipart upload) are exercised before deployment.

## Options considered

1. **Local filesystem / NFS** — trivial locally, but nothing about it resembles cloud
   object storage; every S3 assumption would be discovered in production.
2. **Cloud storage directly, even in development** — accurate, but requires credentials and
   network for every developer and every CI run, and costs money.
3. **MinIO locally, S3-compatible API, cloud-agnostic port.**

## Decision

Option 3. An `ObjectStore` port defines `put`, `get`, `delete`, `exists`, `presign`,
`list`. Adapters: `MinioObjectStore` (boto3, S3 API), `S3ObjectStore`, `AzureBlobStore`,
`GcsObjectStore`, and `LocalObjectStore` for fast unit tests.

`STORAGE_BACKEND` in configuration selects the adapter at the composition root. No
application or domain code imports boto3.

Bucket layout: `factoryai-raw`, `factoryai-datasets`, `factoryai-artifacts`,
`factoryai-heatmaps`. Keys are content-addressed:
`{category}/{yyyy}/{mm}/{checksum}.{ext}`.

## Consequences

**Positive:** MinIO speaks the S3 API, so the migration to AWS is a credential and endpoint
change; local development is offline and free; content-addressed keys make deduplication
and integrity checks natural.

**Negative:** Azure and GCS are not S3-compatible, so those adapters are genuinely separate
implementations and will be the least-tested paths until someone needs them. Presigned-URL
semantics differ subtly across providers; the port exposes only the common subset.
