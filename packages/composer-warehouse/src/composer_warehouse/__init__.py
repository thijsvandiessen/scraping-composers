"""Warehouse tier: the silver staging database.

Ingestion writes every source's records here verbatim with provenance
(``models`` + ``ingestion``); ``persons`` and ``works`` resolve and dedupe
entities; ``normalize`` provides the shared dedup keys. The gold tier promotes
a curated copy from this database.
"""
