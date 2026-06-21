from abc import ABC, abstractmethod
from typing import Iterator, Generic, TypeVar
from composer_ingest.models.document import Document

T = TypeVar('T')

class SourceAdapter(ABC, Generic[T]):
    """
    Abstract base class for all source ingestion adapters.
    Source-specific fetching, pagination, and parsing logic lives here.
    """
    
    @property
    @abstractmethod
    def source_name(self) -> str:
        """Returns the unique name of the source."""
        pass

    @abstractmethod
    def fetch(self) -> Iterator[Document[T]]:
        """
        Executes the scraping logic and yields normalized Document instances.
        """
        pass
