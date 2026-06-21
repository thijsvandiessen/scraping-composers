from composer_ingest.ingestion.pipeline import IngestionPipeline
from composer_ingest.sources.example_adapter import ExampleAdapter

def main():
    # 1. Initialize the core pipeline
    pipeline = IngestionPipeline()
    
    # 2. Register source adapters
    pipeline.register_adapter(ExampleAdapter())
    
    # 3. Execute the pipeline
    # To run all: documents = pipeline.run_all()
    documents = pipeline.run_source("example_source")
    
    # 4. Handle results generically
    for doc in documents:
        print(f"Ingested [{doc.id}] from {doc.source_name} at {doc.ingested_at}")

if __name__ == "__main__":
    main()
