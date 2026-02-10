"""CLI to rebuild the FAISS RAG index."""

import argparse
import asyncio
from pathlib import Path

from app.services.rag_builder import build_rag_index


def main() -> None:
    parser = argparse.ArgumentParser(description="Build FAISS RAG index from articles.")
    parser.add_argument(
        "--source",
        type=str,
        default=None,
        help="Source directory of articles (default: data/articles)",
    )
    parser.add_argument("--chunk-size", type=int, default=1000)
    parser.add_argument("--overlap", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()

    source = Path(args.source) if args.source else None

    count = asyncio.run(build_rag_index(
        source_dir=source,
        chunk_size=args.chunk_size,
        overlap=args.overlap,
        batch_size=args.batch_size,
    ))

    print(f"Indexed {count} chunks.")


if __name__ == "__main__":
    main()
