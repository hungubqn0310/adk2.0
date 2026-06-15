"""
RAG retrieval tool wrapper for Google ADK Agent integration.
"""

import os
import logging
import time
from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .rag import GemmaQdrantRAG

logger = logging.getLogger(__name__)

_rag_instance = None

def _get_embedding_config() -> tuple[str, int]:
    """Load embedding model and dim from system_config. Falls back to defaults."""
    try:
        from mmvn_b2c_agent.api.admin_config import _get_config
        cfg = _get_config("rag_config") or {}
        model = cfg.get("embedding_model", "gemini-embedding-001")
        dim = cfg.get("embedding_dim", 768)
        return model, dim
    except Exception as e:
        logger.warning(f"Could not load RAG embedding config from DB, using defaults: {e}")
        return "gemini-embedding-001", 768


def _get_rag_instance():
    """Lazy-load RAG instance to avoid import-time initialization issues."""
    global _rag_instance

    if _rag_instance is None:
        # Import here to avoid loading at module import time
        from .rag import GemmaQdrantRAG

        qdrant_url = os.getenv("QDRANT_URL", "http://mmvn-qdrant:6333")
        qdrant_api_key = os.getenv("QDRANT_API_KEY", None)
        gemini_api_key = os.getenv("GOOGLE_API_KEY", None)
        collection_name = os.getenv("RAG_COLLECTION_NAME", "mmvn_rag_agent")
        input_dir = os.getenv("RAG_INPUT_DIR", "/opt/app/data/documents")
        embedding_model, embedding_dim = _get_embedding_config()

        logger.info(
            f"Lazy-loading RAG: URL={qdrant_url}, collection={collection_name}, "
            f"embedding={embedding_model} ({embedding_dim}d)"
        )

        _rag_instance = GemmaQdrantRAG(
            name="mm_vietnam_rag",
            description="RAG system for MM Vietnam knowledge base",
            input_dir=input_dir,
            collection_name=collection_name,
            qdrant_url=qdrant_url,
            qdrant_api_key=qdrant_api_key,
            gemini_api_key=gemini_api_key,
            embedding_model=embedding_model,
            embedding_dim=embedding_dim,
            parent_chunk_size=4000,
            child_chunk_size=1000,
            child_overlap=100,
            similarity_top_k=5,
        )
        try:
            _rag_instance.insert_documents(force_reindex=False)
        except Exception as e:
            logger.warning(f"Could not index documents: {e}")

    return _rag_instance


def get_mm_info_by_rag(language:str, query: str, top_k: int = 5) -> Dict[str, Any]:
    """
    Retrieve information about MM Mega Market Vietnam from knowledge base using RAG (Retrieval-Augmented Generation).

    Use this tool to answer ANY questions about MM Vietnam company information, policies, procedures, and guidelines.
    This is the PRIMARY tool for all MM Vietnam related questions.

    Examples of questions to use this tool:
    - Company policies: delivery policy, return/exchange policy, privacy policy, payment methods
    - Store information: locations, operating hours, contact information
    - M-Card program: benefits, registration, usage
    - Purchase procedures: how to order, payment methods, shipping
    - Legal information: terms of use, regulations
    - Any other MM Vietnam company information

    Args:
        language: The language of the user's question. The final answer should be provided in this language.
        query: The question or search query in Vietnamese or English
        top_k: Number of relevant results to return (default: 5)

    Returns:
        Dict containing:
        - results: List of relevant information chunks with scores and metadata
        - count: Number of results found
        - query: The original query
        - status: "success" or "error"
    """
    logger.info(f"🔍 RAG TOOL CALLED with query: {query[:100]}")
    start_time = time.perf_counter()
    try:
        rag = _get_rag_instance()
        results = rag.retrieve(query=query, top_k=top_k)
        elapsed = time.perf_counter() - start_time
        logger.info(f"[PERF LOG] RAG query took {elapsed:.3f}s, found {len(results)} results")

        return {
            "results": results,
            "count": len(results),
            "query": query,
            "status": "success",
            "instruction_for_agent": (
                f"Use the retrieved information to answer the user's question in {language}. "
                "If no relevant information is found, respond politely that you could not find the answer."
            )
        }

    except Exception as e:
        logger.error(f"Error in RAG retrieval: {e}", exc_info=True)
        return {
            "results": [],
            "count": 0,
            "query": query,
            "status": "error",
            "error": str(e)
        }
