from core.data_ingest import rebuild_processed_data
from core.programs import load_program_catalog
from core.rag import build_index


def main() -> None:
    stats = rebuild_processed_data()
    build_index(rebuild_data=False, force_reindex=True)
    programs = load_program_catalog()

    print("RAG data rebuilt.")
    print(f"Input files: {stats.input_files}")
    print(f"Output files: {stats.output_files}")
    print(f"Skipped files: {stats.skipped_files}")
    print(f"Programs loaded: {len(programs)}")


if __name__ == "__main__":
    main()
