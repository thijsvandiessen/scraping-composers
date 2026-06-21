import hashlib
from typing import Iterator, Dict, Any
from composer_ingest.ingestion.source_adapter import SourceAdapter
from composer_ingest.models.document import Document

class ExampleAdapter(SourceAdapter[Dict[str, Any]]):
    @property
    def source_name(self) -> str:
        return "example_source"

    def fetch(self) -> Iterator[Document[Dict[str, Any]]]:
        # Source-specific logic (requests, pagination, parsing)
        raw_items = [
            {"title": "Example Post 1", "url": "https://example.com/1", "meta": {"author": "A"}},
            {"title": "Example Post 2", "url": "https://example.com/2", "meta": {"author": "B"}}
        ]
        
        for item in raw_items:
            # Deterministic ID generation
            doc_id = hashlib.md5(item["url"].encode()).hexdigest()
            
            yield Document(
                id=doc_id,
                url=item["url"],
                source_name=self.source_name,
                body=item
            )
