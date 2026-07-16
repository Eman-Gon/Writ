"""Deterministic source ingestion for StateGuard."""

from .ingest import ingest, ingest_attested_claim

__all__ = ["ingest", "ingest_attested_claim"]
