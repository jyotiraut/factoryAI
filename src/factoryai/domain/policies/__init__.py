"""Policies: business rules that are not CRUD.

The promotion gate (Phase 6), the drift threshold policy (Phase 11) and the ingestion
validation chain (:mod:`.validation`, Phase 3) all live here — each is a rule the domain
enforces on its own terms, evaluated against plain data rather than driven by a request.
"""
