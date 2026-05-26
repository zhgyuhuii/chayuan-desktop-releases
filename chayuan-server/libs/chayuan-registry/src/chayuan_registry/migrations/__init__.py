"""Schema initialisation lives in chayuan_registry.db.init_engine().

Alembic is intentionally deferred to v0.2; SQLAlchemy `create_all` is
sufficient for the current single-table-family schema and lets the
desktop installer ship without an `alembic` dependency tree.
"""
