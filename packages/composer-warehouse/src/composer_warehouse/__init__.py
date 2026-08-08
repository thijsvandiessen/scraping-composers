"""Warehouse tier: the silver staging database.

Ingestion writes every source's records here verbatim with provenance
(``models`` + ``ingestion``); ``works`` resolves work mentions to canonical
compositions; ``normalize`` provides the shared dedup keys entity resolution
runs on. The gold tier promotes a curated copy from this database.
"""
