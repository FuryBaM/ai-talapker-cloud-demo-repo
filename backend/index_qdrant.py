from pathlib import Path

from core.config import QDRANT_COLLECTION, QDRANT_PATH, RAG_CHUNKS_PATH, RAG_DOCUMENTS_PATH
from core.rag import build_index, close_index


def main() -> None:
    index = build_index(rebuild_data=False, force_reindex=True)
    close_index(index)
    print(
        {
            "qdrant_path": str(Path(QDRANT_PATH).resolve()),
            "collection": QDRANT_COLLECTION,
            "documents_path": str(Path(RAG_DOCUMENTS_PATH).resolve()),
            "chunks_path": str(Path(RAG_CHUNKS_PATH).resolve()),
        }
    )


if __name__ == "__main__":
    main()
