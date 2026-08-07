# ADR-0008 — Compute-bound ports are synchronous

**Status:** accepted · **Date:** 2026-08-07

## Context

Every I/O-bound port in the domain (`ObjectStore`, the repositories, `UnitOfWork`) is
`async`, because the whole point of `async` is to give up the CPU while waiting on a
socket or a disk. `ImageCodec` (Phase 3) and `AnomalyDetector` (Phase 5) are different: a
Pillow decode and an Anomalib forward pass hold the CPU (or GPU) the entire time — there is
no wait to yield during.

## Options considered

1. **Make every port `async` for a uniform interface.** Consistent to read, but an `async
   def` that never awaits is a lie: it promises the event loop stays free and does not
   deliver, which is actively misleading to a caller deciding whether to run it inline.
2. **Synchronous ports for compute-bound work; async everywhere else.**
3. **Wrap every compute call in `asyncio.to_thread` inside the port itself,** hiding the
   sync/async distinction from callers entirely.

## Decision

Compute-bound ports (`ImageCodec`, `AnomalyDetector`) are synchronous. A caller on the
event loop (the future FastAPI inference path) is responsible for dispatching the call off
the loop — a worker thread for a quick decode, a Celery task for a training run (ADR-0005)
— not the port.

## Consequences

**Positive:** a port's signature honestly describes what it does; nothing hides a
multi-second training run behind an `await` that looks like it might return in
microseconds. The dispatch decision (thread vs. task queue vs. run inline in a script) is
made once, at the call site that actually knows the latency budget, instead of being
baked into every adapter.

**Negative:** two calling conventions exist in the codebase instead of one, so a new
contributor must learn which ports are which — mitigated by the `factoryai.domain.ports`
package docstring stating the rule directly, and grep-ability: every compute-bound method
signature lacks `async` at a glance.
