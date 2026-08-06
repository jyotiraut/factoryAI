# Developer Guide

## Environment

**Required:** Docker + Compose, Git, Python 3.11 (see [ADR-0007](adr/0007-python-311.md)),
Node 20+ (frontend, from Phase 13).

Dependency management uses [uv](https://docs.astral.sh/uv/). GNU make is not part of a
standard Windows toolchain, so the same task interface exists twice: `Makefile` for CI and
Linux/macOS, `make.ps1` for Windows. **Both must be updated when a target is added.**

```bash
make venv        # Linux/macOS/CI      |  .\make.ps1 venv        # Windows
make install     #                     |  .\make.ps1 install
make quality     # every CI gate       |  .\make.ps1 quality
make up          # full local stack    |  .\make.ps1 up
make down        # stop, keep volumes  |  .\make.ps1 down
make reset       # stop and DESTROY volumes — deletes local data
```

`make install` installs only the `dev` group so the loop stays fast. `make install-all`
adds the ML and infrastructure groups (large — pulls PyTorch) and is needed from Phase 5.

Everything runs in containers; the virtualenv exists for editor integration, linting and
fast unit tests.

## Code standards

| Tool | Enforces | Command |
|---|---|---|
| Ruff | Lint + import order | `make lint` |
| Black | Formatting (line length 100) | `make format` |
| Mypy | Strict typing, no untyped defs | `make typecheck` |
| import-linter | Layer boundaries (ADR-0001) | `make check-layers` |
| pytest | Unit tests, fast, no Docker | `make test` |
| pytest + coverage | Unit + integration, 80% gate | `make test-cov` (requires Docker) |
| pre-commit | Lint/format/type/layer checks + unit tests on staged files | automatic |

The coverage gate runs against unit **and** integration tests together, not unit tests
alone: infrastructure adapters (`factoryai.infrastructure.*`) are exercised by integration
tests against real containers, so unit-only coverage understates them by design.

Non-negotiables:
- **Type hints on every function**, including tests. `Any` requires a comment justifying it.
- **Docstrings** on every public class and function: what it does, what it raises, and any
  non-obvious behaviour. Not a restatement of the signature.
- **No `print`.** Structured logging via `structlog`, with the correlation ID bound.
- **No magic values.** Thresholds, sizes, timeouts and paths come from Pydantic settings.
- **No business logic in routers, DAGs or Celery tasks.** They call use cases.

## Test strategy

```
tests/
├── unit/          Domain + application. No I/O, no containers. Milliseconds.
├── integration/   Adapters against real Postgres/MinIO via testcontainers.
└── e2e/           Full compose stack, API in → prediction out.
```

Rules: one behaviour per test; arrange-act-assert; fakes over mocks for ports (a
`FakeObjectStore` is more honest than a `MagicMock`); every bug fix arrives with the
regression test that would have caught it.

## Commits and branches

Conventional Commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, `ci:`,
optionally scoped (`feat(ingestion): ...`). Branches: `feat/<short-name>`,
`fix/<short-name>`. One logical change per PR.

A PR must state which roadmap phase it belongs to, include tests, update affected docs, and
add an ADR if it makes a non-obvious decision.

## Adding things

**A new model family** — implement `AnomalyDetector`, decorate with
`@register_detector("name")`, add a config in `configs/models/`, add a contract test. No
other file changes.

**A new MVTec category** — add `configs/categories/<name>.yaml`, set `enabled: true`,
ingest the data. No code changes.

**A new storage backend** — implement `ObjectStore`, register it in the composition root,
run the shared adapter contract test suite against it.

**A new validation rule** — implement `ValidationRule`, add it to the chain configuration.
The `IngestImage` use case does not change.
