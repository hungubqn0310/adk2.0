"""
RAG Data Management API — upload, list, delete knowledge base documents.

Flow: Upload file → save to input_dir → chunk → embed → upsert vào Qdrant.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from mmvn_b2c_agent.api.dashboard_auth import require_permission

logger = logging.getLogger(__name__)

rag_management_router = APIRouter(prefix="/admin/knowledge", tags=["rag-management"])

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".csv"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
_INPUT_DIR = os.getenv("RAG_INPUT_DIR", "/opt/app/data/documents")
_sync_lock = asyncio.Lock()


def _get_input_dir() -> Path:
    p = Path(_INPUT_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


class FileInfo(BaseModel):
    filename: str
    size_bytes: int
    doc_tag: Optional[str] = None


class UploadResponse(BaseModel):
    success: bool
    uploaded: List[str]
    failed: List[str]
    message: str


class ListResponse(BaseModel):
    files: List[FileInfo]
    total: int


class DeleteResponse(BaseModel):
    success: bool
    filename: str
    message: str


class SyncResponse(BaseModel):
    success: bool
    indexed: int
    message: str


@rag_management_router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload & Sync knowledge base documents",
)
async def upload_documents(
    files: List[UploadFile] = File(...),
    doc_tag: Optional[str] = Form(None),
    _user: dict = Depends(require_permission("config")),
):
    """
    Upload một hoặc nhiều file (PDF, DOCX, TXT, CSV) vào knowledge base.
    Hệ thống sẽ tự động chunk → embed → upsert vào Qdrant.
    """
    input_dir = _get_input_dir()
    uploaded: List[str] = []
    failed: List[str] = []

    for upload in files:
        filename = Path(upload.filename).name  # strip path traversal
        ext = Path(filename).suffix.lower()

        if ext not in ALLOWED_EXTENSIONS:
            failed.append(f"{filename} (unsupported type '{ext}')")
            continue

        content = await upload.read()
        if len(content) > MAX_FILE_SIZE:
            failed.append(f"{filename} (exceeds 10 MB limit)")
            continue

        dest = input_dir / filename
        try:
            dest.write_bytes(content)
            uploaded.append(filename)
            logger.info(f"Saved uploaded file: {dest}")
        except Exception as e:
            logger.error(f"Failed to save {filename}: {e}")
            failed.append(f"{filename} (save error)")

    if not uploaded:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No files saved. Failures: {failed}",
        )

    # Trigger incremental sync (chỉ index files mới/thay đổi)
    indexed = 0
    try:
        from mmvn_b2c_agent.tools.rag.rag_tool import _get_rag_instance

        async with _sync_lock:
            rag = _get_rag_instance()

            # Nếu có doc_tag: tìm file cũ cùng tag → xóa khỏi disk trước khi parse
            if doc_tag:
                old_filenames = rag._get_filenames_by_tag(doc_tag)
                for old_fn in old_filenames:
                    if old_fn not in uploaded:
                        old_path = input_dir / old_fn
                        if old_path.exists():
                            old_path.unlink()
                            logger.info(f"Removed old file '{old_fn}' (replaced by tag '{doc_tag}')")

            rag._load_and_parse_documents()
            doc_tags_map = {fn: doc_tag for fn in uploaded} if doc_tag else {}
            indexed = rag.insert_documents(force_reindex=False, doc_tags=doc_tags_map)
        logger.info(f"Sync complete: {indexed} new chunks indexed")
    except Exception as e:
        logger.error(f"Sync error after upload: {e}", exc_info=True)
        return UploadResponse(
            success=True,
            uploaded=uploaded,
            failed=failed,
            message=f"Files saved but sync failed: {e}",
        )

    return UploadResponse(
        success=True,
        uploaded=uploaded,
        failed=failed,
        message=f"Upload & sync complete. {indexed} new chunks indexed.",
    )


@rag_management_router.get(
    "/files",
    response_model=ListResponse,
    summary="List indexed knowledge base files",
)
async def list_documents(
    _user: dict = Depends(require_permission("config")),
):
    """Liệt kê các file đang có trong knowledge base input directory."""
    input_dir = _get_input_dir()
    files: List[FileInfo] = []

    try:
        from mmvn_b2c_agent.tools.rag.rag_tool import _get_rag_instance
        file_tags = _get_rag_instance()._get_file_doc_tags()
    except Exception:
        file_tags = {}

    for path in sorted(input_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in ALLOWED_EXTENSIONS:
            files.append(FileInfo(
                filename=path.name,
                size_bytes=path.stat().st_size,
                doc_tag=file_tags.get(path.name) or None,
            ))

    return ListResponse(files=files, total=len(files))


@rag_management_router.delete(
    "/files/{filename}",
    response_model=DeleteResponse,
    summary="Delete a knowledge base document",
)
async def delete_document(
    filename: str,
    _user: dict = Depends(require_permission("config")),
):
    """
    Xóa file khỏi input directory VÀ xóa toàn bộ chunks tương ứng trong Qdrant.
    """
    # Sanitize filename
    safe_name = Path(filename).name
    if safe_name != filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid filename")

    input_dir = _get_input_dir()
    file_path = input_dir / safe_name

    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File '{safe_name}' not found",
        )

    # Xóa chunks khỏi Qdrant trước
    try:
        from mmvn_b2c_agent.tools.rag.rag_tool import _get_rag_instance

        rag = _get_rag_instance()
        rag._delete_chunks_by_file(safe_name)
        logger.info(f"Deleted Qdrant chunks for: {safe_name}")
    except Exception as e:
        logger.error(f"Failed to delete Qdrant chunks for {safe_name}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to remove from vector DB: {e}",
        )

    # Xóa file trên disk
    file_path.unlink()
    logger.info(f"Deleted file: {file_path}")

    return DeleteResponse(
        success=True,
        filename=safe_name,
        message=f"'{safe_name}' deleted from knowledge base.",
    )


@rag_management_router.post(
    "/sync",
    response_model=SyncResponse,
    summary="Re-sync all documents into vector DB",
)
async def sync_documents(
    force: bool = False,
    _user: dict = Depends(require_permission("config")),
):
    """
    Chạy lại quá trình index toàn bộ documents.
    force=true sẽ xóa collection cũ và index lại từ đầu.
    """
    try:
        from mmvn_b2c_agent.tools.rag.rag_tool import _get_rag_instance

        async with _sync_lock:
            rag = _get_rag_instance()
            rag._load_and_parse_documents()
            indexed = rag.insert_documents(force_reindex=force)

        return SyncResponse(
            success=True,
            indexed=indexed,
            message=f"Sync {'(force)' if force else ''} complete. {indexed} chunks in collection.",
        )
    except Exception as e:
        logger.error(f"Sync error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
