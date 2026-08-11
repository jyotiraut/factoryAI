# ADR-0011 — JWT authentication, permission-keyed RBAC, and audit tamper detection

**Status:** accepted · **Date:** 2026-08-07

## Context

Every route Phase 7 shipped is open to anyone who can reach the process. Phase 8 has to
close that without breaking the layering ADR-0001 already established: the domain still
must not know about `pyjwt` or `argon2`, and `User` was deliberately built in Phase 1 with
no password field. Three decisions had no obvious single answer: where a credential hash
actually lives given the entity refuses to carry one, how a role check that is not always
a simple rank threshold gets expressed once instead of at every call site, and how far a
hash-chained audit log (in place since Phase 2) can go towards proving nothing in it has
been tampered with.

## Decisions

**The password hash lives on the `users` row, not on the `User` entity.** `User`'s
docstring already committed to holding no credential — logging or serialising a `User`
must never risk leaking one. The hash still has to be persisted somewhere, so
`UserRepository` gained `set_password_hash`/`get_password_hash` as extra accessors against
the same row `add`/`update`/`get` already own, rather than either putting a hash field on
the entity or standing up a second `credentials` table for what is a 1:1 relationship with
no independent lifecycle. `factoryai.domain.ports.auth.PasswordHasher` (argon2id in
production, via `Argon2PasswordHasher`) is the only thing that ever sees a plaintext
password or a hash; the domain calls it, never `argon2` directly.

**RBAC is keyed by permission, not by role.** `UserRole.can_act_as` is a linear
hierarchy — exactly right for "does this role outrank that one" and exactly wrong for
deciding *which* rank threshold a given action needs. `domain.policies.permissions` adds a
`Permission` enum (`submit_prediction`, `promote_model`, `manage_users`, …) and one
dictionary mapping each to its minimum satisfying role; `has_permission(user, permission)`
is the only function any route, use case, or CLI command ever calls to decide access.
Every existing route gained a `require_permission(...)` FastAPI dependency; none compare
`user.role` directly. The two ROADMAP exit criteria fall out of the matrix without special
casing: an operator's rank does not clear `PROMOTE_MODEL`'s `ml_engineer` floor, and a
viewer's rank does not clear `SUBMIT_FEEDBACK`'s `operator` floor.

**Access tokens carry a role snapshot; authorization decisions never trust it.**
`AccessTokenClaims.role` exists so the token is self-describing, but
`api.dependencies.get_current_user` re-fetches the user from PostgreSQL on every request
and every permission check runs against that fresh row. A role change or deactivation
takes effect on the very next request instead of waiting for that user's current access
token to expire — the alternative (trusting the claim) would mean a demoted or deactivated
account keeps its old privileges for up to `JWT_ACCESS_TOKEN_MINUTES`.

**Only refresh tokens are individually revocable.** Access tokens are short-lived by
design (default 30 minutes) and are never checked against a blacklist — doing so would
mean a database round trip on every single authenticated request, defeating the point of
a self-contained token. `POST /auth/logout` revokes the refresh token's `jti` instead
(`revoked_tokens`, checked only when a refresh is attempted); the practical effect is that
a logout takes up to one access-token lifetime to fully lock out a caller who kept their
last-issued access token, a bound the team judged acceptable.

**Promotion and rollback get an HTTP surface for the first time.** Phase 6 shipped these
as CLI-only. Testing "an operator cannot promote a model" as an HTTP-level exit criterion
needs a route to test against, so `POST /models/{category}/promote` and
`POST /models/{category}/rollback` now exist, gated by `Permission.PROMOTE_MODEL` /
`Permission.ROLLBACK_MODEL` (both `ml_engineer` and above). The CLI commands are untouched
and remain the trusted-operator path that needs no bearer token.

**Tamper detection walks the whole chain, not a windowed sample.**
`AuditRepository.list_all()` plus the existing `domain.entities.audit.verify_chain`
(written in Phase 2, never previously called from application code) is exposed as
`VerifyAuditChain` and `factoryai audit verify`. It recomputes every link and reports the
first sequence number whose `prev_hash` no longer matches its predecessor's recomputed
hash — which also catches a *deleted* row, since removing one leaves a gap the sequence
check (`current.sequence == previous.sequence + 1`) rejects. The one gap this cannot close:
tampering the chain's current tip has no successor to invalidate, the same limitation any
hash chain has without an external anchor (git's own HEAD commit has the identical
property). This is accepted rather than solved — anchoring the tip externally (e.g.
periodically publishing it to a separate immutable log) is real future work, not done here.

**The first administrator is created outside HTTP, deliberately.**
`POST /auth/register` requires `Permission.MANAGE_USERS`, which nobody holds before any
account exists — an open self-service registration endpoint was rejected for an internal
factory tool with a small, admin-managed user list. `factoryai user create` runs the same
`RegisterUser` use case from a trusted local shell instead, which is also how every
account is expected to be created day to day; the HTTP route exists for an already-running
administrator to delegate the same action without shell access to the host.

## Consequences

- `GET /health/live`, `GET /health/ready`, and `GET /metrics` remain unguarded — these are
  operational endpoints polled by infrastructure (container orchestrator, Prometheus), not
  end users, and gating them would mean giving every scraper a bearer token.
- `POST /feedback` no longer accepts a client-supplied `user_id`; the authenticated
  principal is used instead, closing a Phase 7 gap that was documented but not yet fixed
  (a caller could previously attribute feedback to an arbitrary user id, provided it
  existed).
- Every route added in Phases 7 and earlier changed its response for an unauthenticated
  caller from "the intended behaviour" to `401`; this is a breaking API change, expected
  and accepted as the point of the phase.
