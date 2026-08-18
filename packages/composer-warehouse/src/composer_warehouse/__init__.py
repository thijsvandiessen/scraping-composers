"""Warehouse tier: the silver staging database.

Ingestion writes every source's records here verbatim with provenance
(``ingestion``, over the shared ``composer_models`` schema); ``persons`` and
``works`` resolve and dedupe entities using ``composer_models.normalize``'s
dedup keys. The gold tier promotes a curated copy from this database.
"""
