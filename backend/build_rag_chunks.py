from pathlib import Path

from core.config import RAG_CHUNKS_PATH, RAG_DOCUMENTS_PATH
from core.rag import build_rag_chunks


def main() -> None:
    chunks = build_rag_chunks(RAG_DOCUMENTS_PATH, RAG_CHUNKS_PATH)
    print(
        {
            "chunks": len(chunks),
            "documents_path": str(Path(RAG_DOCUMENTS_PATH).resolve()),
            "chunks_path": str(Path(RAG_CHUNKS_PATH).resolve()),
        }
    )


if __name__ == "__main__":
    main()
