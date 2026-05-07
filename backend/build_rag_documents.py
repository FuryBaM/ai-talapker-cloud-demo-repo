from pathlib import Path

from core.config import DATA_DIR, INPUT_DATA_DIR, RAG_DOCUMENTS_PATH
from core.data_ingest import rebuild_processed_data
from core.rag import build_rag_documents


def main() -> None:
    stats = rebuild_processed_data(INPUT_DATA_DIR, DATA_DIR)
    documents = build_rag_documents(DATA_DIR, RAG_DOCUMENTS_PATH)
    print(
        {
            "input_files": stats.input_files,
            "output_files": stats.output_files,
            "skipped_files": stats.skipped_files,
            "documents": len(documents),
            "documents_path": str(Path(RAG_DOCUMENTS_PATH).resolve()),
        }
    )


if __name__ == "__main__":
    main()
