# ADR-0006 — DVC for dataset versioning

**Status:** accepted · **Date:** 2026-08-04

## Context

"Which data produced this model?" must have an exact answer, on a clean machine, months
later. Git cannot hold gigabytes of images. PostgreSQL records *which* images belong to a
version but does not guarantee the bytes are still retrievable and unchanged.

## Options considered

1. **PostgreSQL manifest only** — a dataset version is a list of image IDs. Cheap, but the
   guarantee is only as good as the object store's immutability, and there is no
   single-command checkout.
2. **Git LFS** — versions binaries in Git, but scales poorly at this size and couples data
   history to code history permanently.
3. **DVC with MinIO as the remote.**
4. **lakeFS / Delta Lake** — powerful, but heavy for an image dataset of this scale.

## Decision

DVC, with MinIO as the remote and the `.dvc` pointer files committed to Git.

A `DatasetVersion` row records the DVC hash, the Git commit, a content-checksum-of-
checksums, image count and class balance. The PostgreSQL manifest and the DVC hash are
complementary: the manifest answers *what is in it* in SQL; DVC answers *give me the exact
bytes*.

## Consequences

**Positive:** `git checkout <commit> && dvc pull` reproduces a dataset exactly; data history
and code history are linked through the commit; DVC reuses the MinIO already in the stack,
so no new infrastructure.

**Negative:** DVC adds a second mental model developers must learn alongside Git, and a
forgotten `dvc push` produces a commit that nobody else can reproduce — mitigated by a
pre-push hook and a CI check that every `.dvc` file resolves against the remote. Large
`dvc pull` operations are slow on first checkout; CI caches the DVC cache directory.
