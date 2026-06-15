#!/usr/bin/env python3
"""
Script to setup RAG: create collection and index documents.
Run this before using RAG tool in the agent.

Usage:
    python scripts/setup_rag.py [--force-reindex]
"""

import sys
import os
import argparse
import logging
import dotenv
import mmvn_b2c_agent.tools.rag.rag as rag_module
# from mmvn_b2c_agent.tools.rag.rag import GemmaQdrantRAG, RETRIEVER_MODES

dotenv.load_dotenv(override=True)
# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

# Import directly from module file to avoid loading root_agent
rag_module_path = os.path.join(project_root, 'mmvn_b2c_agent', 'tools', 'rag')
sys.path.insert(0, rag_module_path)


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def setup_rag(force_reindex: bool = False):
    """Setup RAG collection and index documents."""

    qdrant_url = os.getenv("QDRANT_URL", "http://mmvn-qdrant:6333")
    qdrant_api_key = os.getenv("QDRANT_API_KEY", None)
    gemini_api_key = os.getenv("GOOGLE_API_KEY", None)
    collection_name = os.getenv("RAG_COLLECTION_NAME", "mmvn_faq_agent")
    input_dir = os.getenv("RAG_INPUT_DIR", "/opt/app/data/documents")

    if not os.path.exists(input_dir):
        logger.error(f"Input directory not found: {input_dir}")
        logger.error("Please create the directory and add your documents (.txt, .md, .pdf, .docx)")
        return False

    files = []
    for root, dirs, filenames in os.walk(input_dir):
        files.extend([f for f in filenames if f.endswith(('.txt', '.md', '.pdf', '.doc', '.docx'))])

    if not files:
        logger.warning(f"No documents found in {input_dir}")
        logger.warning("Please add .txt, .md, .pdf, or .docx files to the directory")
        return False

    logger.info(f"Found {len(files)} documents to index")

    try:
        # Initialize RAG
        logger.info("\n" + "=" * 70)
        logger.info("Step 1: Initializing RAG system...")
        logger.info("=" * 70)

        rag = rag_module.GemmaQdrantRAG(
            name="mm_vietnam_rag",
            description="RAG system for MM Vietnam knowledge base",
            input_dir=input_dir,
            collection_name=collection_name,
            qdrant_url=qdrant_url,
            qdrant_api_key=qdrant_api_key,
            gemini_api_key=gemini_api_key,
            embedding_model="gemini-embedding-001",
            embedding_dim=768,
            retriever_mode=rag_module.RETRIEVER_MODES.SIMPLE_VECTOR,
            chunk_size=1024,
            chunk_overlap=128,
            similarity_top_k=5,
        )

        rag.insert_documents(force_reindex=force_reindex)
        test_query = "chính sách giao hàng"
        results = rag.retrieve(query=test_query, top_k=3)

        logger.info(f"Test query: '{test_query}'")
        logger.info(f"Retrieved {len(results)} results:")
        for i, result in enumerate(results, 1):
            logger.info(f"\n  Result {i}:")
            logger.info(f"    Score: {result['score']:.4f}")
            logger.info(f"    Text preview: {result['text'][:100]}...")
            logger.info(f"    Metadata: {result['metadata']}")

        logger.info("\n" + "=" * 70)
        logger.info("✅ RAG setup completed successfully!")
        logger.info("=" * 70)
        return True

    except Exception as e:
        logger.error(f"\n❌ Error during RAG setup: {e}", exc_info=True)
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Setup RAG collection and index documents for MM Vietnam chatbot"
    )
    parser.add_argument(
        '--force-reindex',
        action='store_true',
        help='Force reindex all documents (delete existing collection)'
    )

    args = parser.parse_args()

    success = setup_rag(force_reindex=args.force_reindex)

    if success:
        logger.info("\n✓ You can now use the RAG tool in your agent!")
        sys.exit(0)
    else:
        logger.error("\n✗ RAG setup failed. Please check the errors above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
