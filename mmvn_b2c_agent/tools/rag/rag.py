"""
RAG System — Parent-Child architecture (học từ ai-knowledge-hub).

Architecture:
  - Child chunks (~1000 chars): indexed in Qdrant with dense vectors → precise retrieval
  - Parent chunks (~4000 chars): stored in Qdrant without search → rich context for LLM

Retrieval pipeline:
  1. Embed query with task_type=RETRIEVAL_QUERY
  2. Search child collection for precise matches
  3. Deduplicate by parent_id (keep best-score child per parent)
  4. Fetch parent chunks by ID → return full context to agent

Key improvements over V1 (flat chunking):
  - Task type differentiation: RETRIEVAL_DOCUMENT vs RETRIEVAL_QUERY
  - Smart boundary detection: no mid-sentence / mid-link cuts
  - Context prefix on child chunks: improves embedding quality
  - Header-aware splitting: respects document structure
  - File hash change detection: skip unchanged files
"""

from __future__ import annotations

import csv
import hashlib
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import fitz
import docx2txt
import numpy as np
from google import genai
from google.genai import types as genai_types
from google.genai.types import HttpOptions
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    OptimizersConfigDiff,
    PointStruct,
    VectorParams,
)

logger = logging.getLogger(__name__)

_GEMINI_BASE_URL = os.getenv("GOOGLE_GEMINI_BASE_URL")

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ParentChunk:
    id: str
    content: str
    parent_index: int
    child_ids: List[str] = field(default_factory=list)
    header_path: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChildChunk:
    id: str
    content: str
    chunk_index: int
    parent_id: str
    header_path: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

class GoogleGeminiEmbedding:
    """Google Gemini embeddings với task_type differentiation."""

    def __init__(self, model_name: str = "gemini-embedding-001", output_dim: int = 768):
        self.model_name = model_name
        self.output_dim = output_dim
        self.client = genai.Client(
            http_options=HttpOptions(base_url=_GEMINI_BASE_URL) if _GEMINI_BASE_URL else None,
        )
        logger.info(f"Initialized Gemini embedding: {model_name}, dim={output_dim}")

    def _embed_batch(self, texts: List[str], task_type: str) -> List[List[float]]:
        """Embed a batch of texts in a single API call."""
        result = self.client.models.embed_content(
            model=self.model_name,
            contents=texts,
            config=genai_types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=self.output_dim,
            ),
        )
        return [e.values for e in result.embeddings]

    def encode_documents(self, texts: List[str], batch_size: int = 16) -> np.ndarray:
        """Encode documents for indexing (RETRIEVAL_DOCUMENT)."""
        all_embeddings: List[List[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            all_embeddings.extend(self._embed_batch(batch, "RETRIEVAL_DOCUMENT"))
        return np.array(all_embeddings)

    def encode_query(self, query: str) -> List[float]:
        """Encode a search query (RETRIEVAL_QUERY)."""
        result = self.client.models.embed_content(
            model=self.model_name,
            contents=query,
            config=genai_types.EmbedContentConfig(
                task_type="RETRIEVAL_QUERY",
                output_dimensionality=self.output_dim,
            ),
        )
        return result.embeddings[0].values

    # --- Legacy compat (encode without task_type) ---
    def encode(self, texts, batch_size: int = 16, **kwargs) -> np.ndarray:
        """Backward-compat: encode without explicit task_type (uses RETRIEVAL_DOCUMENT)."""
        if isinstance(texts, str):
            texts = [texts]
        return self.encode_documents(texts, batch_size=batch_size)


# ---------------------------------------------------------------------------
# Chunking helpers
# ---------------------------------------------------------------------------

def _find_good_break(text: str, target_pos: int, search_range: int = 200) -> int:
    """Find the best break point near target_pos.
    Priority: paragraph → line → sentence → word boundary.
    """
    if target_pos >= len(text):
        return len(text)

    lo = max(0, target_pos - search_range)
    hi = min(len(text), target_pos + 50)
    window = text[lo:hi]

    def find_best(pattern: str) -> Optional[int]:
        candidates = [lo + m.end() for m in re.finditer(pattern, window)]
        before = [p for p in candidates if p <= target_pos + 50]
        return max(before) if before else None

    for pattern in [r'\n\n', r'\n', r'[.!?:;]\s', r'\s']:
        bp = find_best(pattern)
        if bp is not None:
            return bp

    return target_pos


def _ensure_no_broken_links(text: str) -> str:
    """Trim trailing broken markdown links."""
    m = re.search(r'!?\[[^\]]*\]\([^)]*$', text)
    if m:
        return text[: m.start()].rstrip()
    m = re.search(r'!?\[[^\]]*$', text)
    if m and '\n' not in text[m.start():]:
        return text[: m.start()].rstrip()
    return text


def _clean_chunk_start(text: str) -> str:
    """Fix orphaned URL parts or mid-sentence starts at chunk beginning."""
    if not text or not text.strip():
        return text

    m = re.match(r'^[^\s\n)]*\)\s*', text)
    if m and not text.startswith('(') and not text.startswith('['):
        text = text[m.end():]

    m = re.match(r'^\([^)]*\)\s*', text)
    if m:
        text = text[m.end():]

    first_char = text.lstrip()[0] if text.strip() else ''
    if first_char.islower():
        m = re.search(r'\n', text)
        if m and m.start() < 200:
            return text[m.end():]
        m = re.search(r'[.!?]\s+[A-ZÀ-Ỹ#*\-|!]', text)
        if m and m.start() < 300:
            return text[m.start() + 2:]

    return text


def _split_by_headers(text: str) -> List[Dict[str, Any]]:
    """Split document by markdown headers, preserving header hierarchy."""
    sections: List[Dict[str, Any]] = []
    current_headers: List[str] = []
    current_lines: List[str] = []

    for line in text.split('\n'):
        m = re.match(r'^(#{1,4})\s+(.*)', line)
        if m:
            # Flush current section
            if current_lines:
                content = '\n'.join(current_lines).strip()
                if content:
                    sections.append({'content': content, 'headers': current_headers[:]})
            # Update header path
            level = len(m.group(1))  # 1-4
            header_text = m.group(2).strip()
            current_headers = current_headers[: level - 1] + [header_text]
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        content = '\n'.join(current_lines).strip()
        if content:
            sections.append({'content': content, 'headers': current_headers[:]})

    return sections if sections else [{'content': text, 'headers': []}]


def _split_into_children(
    parent_content: str,
    parent_id: str,
    headers: List[str],
    start_index: int,
    child_chunk_size: int,
    child_overlap: int,
    document_title: str = "",
) -> List[ChildChunk]:
    """Split a parent chunk into smaller child chunks with smart boundaries."""
    context_parts = []
    if document_title:
        context_parts.append(document_title)
    if headers:
        context_parts.extend(headers)
    context_prefix = "[Document: " + " > ".join(context_parts) + "]\n" if context_parts else ""

    if len(parent_content) <= child_chunk_size:
        return [ChildChunk(
            id=str(uuid.uuid4()),
            content=context_prefix + parent_content,
            chunk_index=start_index,
            parent_id=parent_id,
            header_path=headers,
        )]

    children: List[ChildChunk] = []
    start = 0
    idx = start_index
    text_len = len(parent_content)

    while start < text_len:
        raw_end = min(start + child_chunk_size, text_len)
        end = text_len if raw_end >= text_len else _find_good_break(parent_content, raw_end)

        chunk_text = parent_content[start:end].strip()
        chunk_text = _ensure_no_broken_links(chunk_text)

        if start > 0 and chunk_text:
            chunk_text = _clean_chunk_start(chunk_text).strip()

        if chunk_text:
            children.append(ChildChunk(
                id=str(uuid.uuid4()),
                content=context_prefix + chunk_text,
                chunk_index=idx,
                parent_id=parent_id,
                header_path=headers,
            ))
            idx += 1

        if end >= text_len:
            break
        start = max(start + child_chunk_size // 2, end - child_overlap)

    return children


def _create_parent_child_chunks(
    text: str,
    document_title: str,
    parent_chunk_size: int,
    child_chunk_size: int,
    child_overlap: int,
) -> Tuple[List[ParentChunk], List[ChildChunk]]:
    """
    Full parent-child chunking pipeline.

    1. Split by markdown headers (preserves document structure)
    2. Accumulate sections into parent-sized groups
    3. Split each parent into child-sized chunks with overlap
    """
    sections = _split_by_headers(text)

    parents: List[ParentChunk] = []
    children: List[ChildChunk] = []
    parent_index = 0
    child_index = 0

    current_parts: List[str] = []
    current_headers: List[str] = []
    current_len = 0

    def flush_parent() -> None:
        nonlocal parent_index, child_index, current_parts, current_headers, current_len
        if not current_parts:
            return

        parent_content = '\n\n'.join(current_parts)
        if document_title and parent_index == 0:
            parent_content = f"# {document_title}\n\n{parent_content}"

        parent_id = str(uuid.uuid4())
        parent_children = _split_into_children(
            parent_content=parent_content,
            parent_id=parent_id,
            headers=current_headers[:],
            start_index=child_index,
            child_chunk_size=child_chunk_size,
            child_overlap=child_overlap,
            document_title=document_title,
        )

        parent = ParentChunk(
            id=parent_id,
            content=parent_content,
            parent_index=parent_index,
            child_ids=[c.id for c in parent_children],
            header_path=current_headers[:],
            metadata={'document_title': document_title},
        )
        parents.append(parent)
        children.extend(parent_children)

        parent_index += 1
        child_index += len(parent_children)
        current_parts.clear()
        current_headers.clear()
        current_len = 0

    for section in sections:
        content = section['content']
        headers = section['headers']
        sec_len = len(content)

        if current_len + sec_len > parent_chunk_size and current_parts:
            flush_parent()

        current_parts.append(content)
        if headers and not current_headers:
            current_headers = headers
        current_len += sec_len

    flush_parent()

    logger.info(f"Chunked '{document_title}': {len(parents)} parents, {len(children)} children")
    return parents, children


# ---------------------------------------------------------------------------
# CSV chunking
# ---------------------------------------------------------------------------

def _create_csv_parent_child_chunks(
    file_path: str,
    document_title: str,
    parent_chunk_size: int,
    child_chunk_size: int,
) -> Tuple[List[ParentChunk], List[ChildChunk]]:
    """
    CSV-aware parent-child chunking. Each row is an atomic unit — never cut mid-row.

    - Child chunk: groups of rows up to child_chunk_size chars, header prepended
    - Parent chunk: groups of child chunks up to parent_chunk_size chars
    Headers are repeated in every chunk so each chunk is self-contained.
    """
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f)
            headers = list(reader.fieldnames or [])
            rows = [dict(row) for row in reader]
    except Exception as e:
        logger.warning(f"CSV parse error {file_path}: {e}")
        return [], []

    if not rows or not headers:
        logger.warning(f"Empty CSV or no headers: {file_path}")
        return [], []

    header_line = " | ".join(headers)
    context_prefix = f"[Document: {document_title}]\n{header_line}\n"
    prefix_len = len(context_prefix)

    def format_row(row: Dict[str, str]) -> str:
        return " | ".join(f"{h}: {(row.get(h) or '').strip()}" for h in headers)

    row_texts = [format_row(r) for r in rows]

    # Build child chunks: group rows until child_chunk_size reached
    raw_children: List[str] = []
    current_rows: List[str] = []
    current_len = prefix_len

    for row_text in row_texts:
        row_len = len(row_text) + 1  # +1 for newline
        if current_rows and current_len + row_len > child_chunk_size:
            raw_children.append(context_prefix + "\n".join(current_rows))
            current_rows = []
            current_len = prefix_len
        current_rows.append(row_text)
        current_len += row_len

    if current_rows:
        raw_children.append(context_prefix + "\n".join(current_rows))

    # Build parent chunks: group child chunks until parent_chunk_size reached
    parents: List[ParentChunk] = []
    children: List[ChildChunk] = []
    parent_index = 0
    child_index = 0
    pending: List[str] = []
    pending_len = 0

    def _flush_parent(batch: List[str]) -> None:
        nonlocal parent_index, child_index
        parent_id = str(uuid.uuid4())
        batch_children = [
            ChildChunk(
                id=str(uuid.uuid4()),
                content=content,
                chunk_index=child_index + idx,
                parent_id=parent_id,
                header_path=[document_title],
            )
            for idx, content in enumerate(batch)
        ]
        parents.append(ParentChunk(
            id=parent_id,
            content="\n\n".join(batch),
            parent_index=parent_index,
            child_ids=[c.id for c in batch_children],
            header_path=[document_title],
            metadata={'document_title': document_title},
        ))
        children.extend(batch_children)
        parent_index += 1
        child_index += len(batch_children)

    for child_content in raw_children:
        content_len = len(child_content)
        if pending and pending_len + content_len > parent_chunk_size:
            _flush_parent(pending)
            pending = []
            pending_len = 0
        pending.append(child_content)
        pending_len += content_len

    if pending:
        _flush_parent(pending)

    logger.info(
        f"CSV chunked '{document_title}': {len(rows)} rows → "
        f"{len(parents)} parents, {len(children)} children"
    )
    return parents, children


# ---------------------------------------------------------------------------
# Main RAG class
# ---------------------------------------------------------------------------

# noinspection PyPep8Naming
class RETRIEVER_MODES(str, Enum):
    PARENT_CHILD = "parent_child"
    SIMPLE_VECTOR = "simple_vector"   # legacy fallback


PARENT_DUMMY_VECTOR_SIZE = 4


class GemmaQdrantRAG:
    """
    RAG system — Parent-Child architecture với Google Gemini + Qdrant.

    Collections:
        {collection_name}_child  — child chunks (dense vectors, searchable)
        {collection_name}_parent — parent chunks (dummy vector, storage only)

    Usage:
        rag = GemmaQdrantRAG(...)
        rag.insert_documents()       # index files in input_dir
        results = rag.retrieve(query)
    """

    def __init__(
        self,
        *,
        name: str,
        description: str,
        input_dir: str,
        collection_name: str = "mmvn_rag",
        qdrant_url: Optional[str] = None,
        qdrant_api_key: Optional[str] = None,
        embedding_model: str = "gemini-embedding-001",
        embedding_dim: int = 768,
        retriever_mode: RETRIEVER_MODES = RETRIEVER_MODES.PARENT_CHILD,
        # Parent-child sizes (characters)
        parent_chunk_size: int = 4000,
        child_chunk_size: int = 1000,
        child_overlap: int = 100,
        similarity_top_k: int = 5,
        # Legacy compat params (ignored in parent_child mode)
        chunk_size: int = 1024,
        chunk_overlap: int = 128,
        gemini_api_key: Optional[str] = None,
    ):
        self.name = name
        self.description = description
        self.input_dir = input_dir
        self.collection_name = collection_name
        self.embedding_dim = embedding_dim
        self.retriever_mode = retriever_mode
        self.parent_chunk_size = parent_chunk_size
        self.child_chunk_size = child_chunk_size
        self.child_overlap = child_overlap
        self.similarity_top_k = similarity_top_k

        self.client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
        self.dense_model = GoogleGeminiEmbedding(embedding_model, output_dim=embedding_dim)

        self._load_and_parse_documents()
        self._setup_collections()
        logger.info("RAG system initialized (parent-child mode)")

    # -----------------------------------------------------------------------
    # Collection names
    # -----------------------------------------------------------------------

    @property
    def _child_collection(self) -> str:
        return f"{self.collection_name}_child"

    @property
    def _parent_collection(self) -> str:
        return f"{self.collection_name}_parent"

    # -----------------------------------------------------------------------
    # File parsing
    # -----------------------------------------------------------------------

    def _compute_file_hash(self, file_path: str) -> str:
        h = hashlib.md5()
        try:
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception as e:
            logger.warning(f"Could not hash {file_path}: {e}")
            return ""

    def _parse_pdf(self, file_path: str) -> str:
        try:
            doc = fitz.open(file_path)
            text = "".join(page.get_text() for page in doc)
            doc.close()
            return text
        except Exception as e:
            logger.warning(f"PDF parse error {file_path}: {e}")
            return ""

    def _parse_docx(self, file_path: str) -> str:
        try:
            return docx2txt.process(file_path) or ""
        except Exception as e:
            logger.warning(f"DOCX parse error {file_path}: {e}")
            return ""

    def _load_and_parse_documents(self) -> None:
        """
        Load all documents from input_dir and build parent-child chunks.
        Stores self._file_records: list of (file_hash, document_title, parents, children).
        """
        logger.info(f"Loading documents from: {self.input_dir}")
        # Each item: (filename, file_hash, parents, children)
        self._file_records: List[Tuple[str, str, List[ParentChunk], List[ChildChunk]]] = []
        total_parents = total_children = 0

        for root, _, files in os.walk(self.input_dir):
            for file in sorted(files):
                ext = file.lower().rsplit('.', 1)[-1] if '.' in file else ''
                if ext not in ('txt', 'md', 'pdf', 'doc', 'docx', 'csv'):
                    continue

                file_path = os.path.join(root, file)
                try:
                    file_hash = self._compute_file_hash(file_path)
                    document_title = os.path.splitext(file)[0]

                    if ext == 'csv':
                        parents, children = _create_csv_parent_child_chunks(
                            file_path=file_path,
                            document_title=document_title,
                            parent_chunk_size=self.parent_chunk_size,
                            child_chunk_size=self.child_chunk_size,
                        )
                    else:
                        if ext == 'pdf':
                            text = self._parse_pdf(file_path)
                        elif ext in ('doc', 'docx'):
                            text = self._parse_docx(file_path)
                        else:
                            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                                text = f.read()

                        if not text.strip():
                            logger.warning(f"Empty content: {file}")
                            continue

                        parents, children = _create_parent_child_chunks(
                            text=text,
                            document_title=document_title,
                            parent_chunk_size=self.parent_chunk_size,
                            child_chunk_size=self.child_chunk_size,
                            child_overlap=self.child_overlap,
                        )

                    if not parents:
                        logger.warning(f"Empty content: {file}")
                        continue

                    self._file_records.append((file, file_hash, parents, children))
                    total_parents += len(parents)
                    total_children += len(children)

                except Exception as e:
                    logger.warning(f"Could not read {file_path}: {e}")

        logger.info(
            f"Loaded {len(self._file_records)} files → "
            f"{total_parents} parents, {total_children} children"
        )

    # -----------------------------------------------------------------------
    # Qdrant collections
    # -----------------------------------------------------------------------

    def _setup_collections(self) -> None:
        """Create child + parent collections if not exist."""
        from qdrant_client.http.exceptions import UnexpectedResponse

        existing = {c.name for c in self.client.get_collections().collections}

        # Child collection — dense vectors, searchable
        if self._child_collection not in existing:
            self.client.create_collection(
                collection_name=self._child_collection,
                vectors_config={
                    "dense": VectorParams(size=self.embedding_dim, distance=Distance.COSINE)
                },
                optimizers_config=OptimizersConfigDiff(indexing_threshold=5000),
            )
            logger.info(f"Created child collection: {self._child_collection}")

        # Parent collection — dummy vector, storage only
        if self._parent_collection not in existing:
            self.client.create_collection(
                collection_name=self._parent_collection,
                vectors_config=VectorParams(
                    size=PARENT_DUMMY_VECTOR_SIZE, distance=Distance.COSINE
                ),
                optimizers_config=OptimizersConfigDiff(indexing_threshold=5000),
            )
            logger.info(f"Created parent collection: {self._parent_collection}")

    # -----------------------------------------------------------------------
    # Indexing
    # -----------------------------------------------------------------------

    def _get_indexed_file_hashes(self) -> Dict[str, str]:
        """Return {filename: file_hash} of files already indexed in child collection."""
        try:
            indexed: Dict[str, str] = {}
            offset = None
            while True:
                records, offset = self.client.scroll(
                    collection_name=self._child_collection,
                    limit=100,
                    offset=offset,
                    with_payload=['filename', 'file_hash'],
                )
                for r in records:
                    fn = r.payload.get('filename')
                    fh = r.payload.get('file_hash')
                    if fn and fh:
                        indexed[fn] = fh
                if offset is None:
                    break
            return indexed
        except Exception as e:
            logger.warning(f"Could not get indexed hashes: {e}")
            return {}

    def _delete_chunks_by_file(self, filename: str) -> None:
        """Delete all child + parent chunks for a given filename."""
        doc_filter = Filter(
            must=[FieldCondition(key="filename", match=MatchValue(value=filename))]
        )
        for col in (self._child_collection, self._parent_collection):
            try:
                self.client.delete(collection_name=col, points_selector=doc_filter)
                logger.info(f"Deleted chunks of '{filename}' from {col}")
            except Exception as e:
                logger.warning(f"Could not delete from {col}: {e}")

    def _delete_chunks_by_tag(self, doc_tag: str) -> None:
        """Delete all child + parent chunks for a given doc_tag."""
        tag_filter = Filter(
            must=[FieldCondition(key="doc_tag", match=MatchValue(value=doc_tag))]
        )
        for col in (self._child_collection, self._parent_collection):
            try:
                self.client.delete(collection_name=col, points_selector=tag_filter)
                logger.info(f"Deleted chunks with tag '{doc_tag}' from {col}")
            except Exception as e:
                logger.warning(f"Could not delete tag '{doc_tag}' from {col}: {e}")

    def _get_filenames_by_tag(self, doc_tag: str) -> List[str]:
        """Return list of filenames currently indexed with a given doc_tag."""
        filenames: set = set()
        offset = None
        try:
            while True:
                records, offset = self.client.scroll(
                    collection_name=self._child_collection,
                    scroll_filter=Filter(
                        must=[FieldCondition(key="doc_tag", match=MatchValue(value=doc_tag))]
                    ),
                    limit=100,
                    offset=offset,
                    with_payload=["filename"],
                )
                for r in records:
                    fn = r.payload.get("filename")
                    if fn:
                        filenames.add(fn)
                if offset is None:
                    break
        except Exception as e:
            logger.warning(f"Could not get filenames for tag '{doc_tag}': {e}")
        return list(filenames)

    def _get_file_doc_tags(self) -> Dict[str, str]:
        """Return {filename: doc_tag} for all indexed files that have a non-empty tag."""
        result: Dict[str, str] = {}
        offset = None
        try:
            while True:
                records, offset = self.client.scroll(
                    collection_name=self._child_collection,
                    limit=100,
                    offset=offset,
                    with_payload=["filename", "doc_tag"],
                )
                for r in records:
                    fn = r.payload.get("filename")
                    tag = r.payload.get("doc_tag")
                    if fn and tag and fn not in result:
                        result[fn] = tag
                if offset is None:
                    break
        except Exception as e:
            logger.warning(f"Could not get file doc_tags: {e}")
        return result

    def insert_documents(self, force_reindex: bool = False, doc_tags: Optional[Dict[str, str]] = None) -> int:
        """
        Index documents into Qdrant.
        Only indexes new or changed files (based on file hash) unless force_reindex=True.

        doc_tags: {filename: doc_tag} — files with a tag replace ALL existing chunks
                  sharing that tag (regardless of old filename). Files without a tag
                  use the existing filename+hash dedup logic unchanged.

        Returns:
            Number of child chunks actually upserted.
        """
        doc_tags = doc_tags or {}

        if force_reindex:
            logger.info("Force re-index: dropping existing collections…")
            for col in (self._child_collection, self._parent_collection):
                try:
                    self.client.delete_collection(col)
                except Exception:
                    pass
            self._setup_collections()
            indexed_hashes: Dict[str, str] = {}
        else:
            indexed_hashes = self._get_indexed_file_hashes()
            logger.info(f"Already indexed: {len(indexed_hashes)} files")

        total_indexed = 0

        for filename, file_hash, parents, children in self._file_records:
            tag = doc_tags.get(filename)

            if tag:
                # Tag-based replace: xóa toàn bộ chunks cũ cùng tag → index file mới
                self._delete_chunks_by_tag(tag)
                logger.info(f"  Replace by tag '{tag}': {filename}")
            else:
                # Giữ nguyên logic cũ: dedup theo filename + file_hash
                existing_hash = indexed_hashes.get(filename)
                if existing_hash == file_hash:
                    logger.info(f"  Skip (unchanged): {filename}")
                    continue
                if existing_hash and existing_hash != file_hash:
                    logger.info(f"  Update (hash changed): {filename}")
                    self._delete_chunks_by_file(filename)
                else:
                    logger.info(f"  Index (new): {filename}")

            if not children:
                logger.warning(f"  No chunks produced for '{filename}' — skipping")
                continue

            # Embed children with RETRIEVAL_DOCUMENT task type
            child_texts = [c.content for c in children]
            logger.info(f"  Embedding {len(child_texts)} child chunks for '{filename}'…")
            dense_embeddings = self.dense_model.encode_documents(child_texts)

            # Upsert children
            child_points = [
                PointStruct(
                    id=child.id,
                    vector={"dense": dense_embeddings[i].tolist()},
                    payload={
                        "text": child.content,
                        "filename": filename,
                        "file_hash": file_hash,
                        "doc_tag": tag or "",
                        "chunk_index": child.chunk_index,
                        "parent_id": child.parent_id,
                        "header_path": child.header_path,
                        "chunk_type": "child",
                    },
                )
                for i, child in enumerate(children)
            ]
            self.client.upsert(collection_name=self._child_collection, points=child_points)

            # Upsert parents (dummy vector, no embedding needed)
            parent_points = [
                PointStruct(
                    id=parent.id,
                    vector=[0.0] * PARENT_DUMMY_VECTOR_SIZE,
                    payload={
                        "text": parent.content,
                        "filename": filename,
                        "file_hash": file_hash,
                        "doc_tag": tag or "",
                        "parent_index": parent.parent_index,
                        "child_ids": parent.child_ids,
                        "header_path": parent.header_path,
                        "chunk_type": "parent",
                    },
                )
                for parent in parents
            ]
            self.client.upsert(collection_name=self._parent_collection, points=parent_points)

            total_indexed += len(children)
            logger.info(
                f"  Indexed '{filename}': {len(parents)} parents, {len(children)} children"
            )

        logger.info(f"insert_documents complete: {total_indexed} child chunks upserted")
        return total_indexed

    # -----------------------------------------------------------------------
    # Retrieval
    # -----------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve relevant documents for a query.

        Parent-child flow:
          1. Embed query with RETRIEVAL_QUERY task type
          2. Search child collection for precise matches
          3. Deduplicate by parent_id (keep best score per parent)
          4. Fetch parent chunks by ID → return rich context
        """
        if top_k is None:
            top_k = self.similarity_top_k

        if self.retriever_mode == RETRIEVER_MODES.PARENT_CHILD:
            return self._parent_child_retrieve(query, top_k)
        else:
            return self._dense_only_retrieve(query, top_k)

    def _parent_child_retrieve(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        """Parent-child retrieval: search children → fetch parents."""
        # Step 1: Embed with RETRIEVAL_QUERY
        query_vector = self.dense_model.encode_query(query)

        # Step 2: Search child collection (get more candidates)
        child_results = self.client.query_points(
            collection_name=self._child_collection,
            query=query_vector,
            using="dense",
            limit=top_k * 3,
            with_payload=True,
        )

        if not child_results.points:
            return []

        # Step 3: Deduplicate by parent_id — keep best-scoring child per parent
        parent_best: Dict[str, Any] = {}
        for point in child_results.points:
            parent_id = point.payload.get("parent_id")
            if not parent_id:
                continue
            existing = parent_best.get(parent_id)
            if existing is None or point.score > existing["score"]:
                parent_best[parent_id] = {
                    "score": point.score,
                    "child_content": point.payload.get("text", ""),
                    "chunk_index": point.payload.get("chunk_index"),
                    "header_path": point.payload.get("header_path", []),
                    "filename": point.payload.get("filename", ""),
                    "parent_id": parent_id,
                }

        # Sort and take top_k
        sorted_results = sorted(
            parent_best.values(), key=lambda x: x["score"], reverse=True
        )[:top_k]

        # Step 4: Fetch parent chunks by ID
        parent_ids = [r["parent_id"] for r in sorted_results]
        parent_map: Dict[str, Any] = {}
        try:
            parent_points = self.client.retrieve(
                collection_name=self._parent_collection,
                ids=parent_ids,
                with_payload=True,
            )
            for p in parent_points:
                parent_map[p.id] = p.payload
        except Exception as e:
            logger.warning(f"Parent retrieval failed: {e}")

        # Step 5: Build final results — use parent content as context
        results = []
        for child_result in sorted_results:
            parent_id = child_result["parent_id"]
            parent_payload = parent_map.get(parent_id, {})

            results.append({
                "id": parent_id,
                "score": child_result["score"],
                "text": parent_payload.get("text", child_result["child_content"]),
                "child_text": child_result["child_content"],
                "metadata": {
                    "filename": child_result["filename"],
                    "header_path": child_result["header_path"],
                    "parent_index": parent_payload.get("parent_index"),
                    "chunk_index": child_result["chunk_index"],
                    "chunk_type": "parent",
                },
            })

        logger.info(
            f"Retrieve '{query[:60]}': "
            f"{len(child_results.points)} children → "
            f"{len(parent_best)} unique parents → "
            f"{len(results)} results"
        )
        return results

    def _dense_only_retrieve(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        """Legacy dense-only retrieval on child collection (flat results)."""
        query_vector = self.dense_model.encode_query(query)
        results = self.client.query_points(
            collection_name=self._child_collection,
            query=query_vector,
            using="dense",
            limit=top_k,
            with_payload=True,
        )
        return [
            {
                "id": str(p.id),
                "score": p.score,
                "text": p.payload.get("text", ""),
                "metadata": {k: v for k, v in p.payload.items() if k != "text"},
            }
            for p in results.points
        ]
