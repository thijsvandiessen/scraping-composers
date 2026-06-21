import logging
from typing import Any, Dict, List
from datetime import datetime, timezone

from composer_ingest.ingestion.source_adapter import SourceAdapter
from composer_ingest.models.document import Document

logger = logging.getLogger(__name__)

class IngestionPipeline:
    """
    Core pipeline to manage and orchestrate registered source adapters.
    """
    def __init__(self):
        self._adapters: Dict[str, SourceAdapter[Any]] = {}

    def register_adapter(self, adapter: SourceAdapter[Any]) -> None:
        """Registers a source adapter with the pipeline."""
        if adapter.source_name in self._adapters:
            logger.warning(f"Overwriting adapter for source: {adapter.source_name}")
        self._adapters[adapter.source_name] = adapter

    def run_all(self) -> List[Document[Any]]:
        """Runs all registered adapters and collects the documents."""
        all_documents = []
        for source_name in self._adapters.keys():
            try:
                documents = self.run_source(source_name)
                all_documents.extend(documents)
            except Exception as e:
                logger.error(f"Failed to ingest from {source_name}: {e}", exc_info=True)
        return all_documents

    def run_source(self, source_name: str) -> List[Document[Any]]:
        """Runs a specific source adapter and returns the ingested documents."""
        if source_name not in self._adapters:
            raise ValueError(f"Source adapter '{source_name}' not registered.")
        
        logger.info(f"Starting ingestion for source: {source_name}")
        adapter = self._adapters[source_name]
        results = []
        
        for doc in adapter.fetch():
            if not doc.ingested_at:
                doc.ingested_at = datetime.now(timezone.utc)
            results.append(doc)
            
        logger.info(f"Completed ingestion for source: {source_name}. Total: {len(results)} docs.")
        return results
