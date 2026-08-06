"""PostgreSQL persistence: SQLAlchemy ORM models, mappers, repositories and the engine.

ORM models (:mod:`.orm`) are deliberately not the domain entities — a mapper
(:mod:`.mappers`) translates between them. This costs a translation layer and buys the
freedom to change the schema without the domain following it, and vice versa (ADR-0001).
"""
