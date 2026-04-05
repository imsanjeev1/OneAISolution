import hashlib
import logging
import re
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import chromadb
from chromadb.api.types import EmbeddingFunction, Documents
from docx import Document
from fastapi import HTTPException, UploadFile
from langchain_chroma import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings
from pypdf import PdfReader

from app.schemas import ChatMessage
from app.services.databricks_service import DatabricksService
from app.settings import Settings


logger = logging.getLogger(__name__)


def get_embeddings() -> HuggingFaceEmbeddings:
    """Load embedding model (768D)."""
    return HuggingFaceEmbeddings(model_name="sentence-transformers/all-mpnet-base-v2")


class FallbackEmbeddingFunction(EmbeddingFunction[Documents]):
    def __init__(self, settings: Settings, databricks_service: DatabricksService) -> None:
        self.settings = settings
        self.databricks_service = databricks_service
        self._huggingface_embeddings: HuggingFaceEmbeddings | None = None
        self._fallback_store: Chroma | None = None

    def _get_huggingface_embeddings(self) -> HuggingFaceEmbeddings:
        if self._huggingface_embeddings is None:
            self._huggingface_embeddings = get_embeddings()
        return self._huggingface_embeddings

    def get_fallback_store(self, collection_name: str, persist_directory: str) -> Chroma:
        if self._fallback_store is None or self._fallback_store._collection.name != collection_name:
            self._fallback_store = Chroma(
                collection_name=collection_name,
                persist_directory=persist_directory,
                embedding_function=self._get_huggingface_embeddings(),
            )
        return self._fallback_store

    def __call__(self, input: Documents) -> list[list[float]]:
        texts = list(input)
        if not texts:
            return []

        try:
            return self.databricks_service.embed_texts(texts)
        except Exception as exc:
            logger.warning("Databricks embeddings failed, falling back to Hugging Face embeddings: %s", exc)
            return self._get_huggingface_embeddings().embed_documents(texts)


class RagService:
    def __init__(self, settings: Settings, databricks_service: DatabricksService) -> None:
        self.settings = settings
        self.databricks_service = databricks_service
        self.client = chromadb.PersistentClient(path=settings.chroma_dir)
        self.embedding_function = FallbackEmbeddingFunction(settings, databricks_service)

    async def ingest_document(self, upload: UploadFile) -> dict[str, str | int]:
        content = await upload.read()
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
        if len(content) > self.settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="Uploaded file is larger than the configured limit.")

        suffix = Path(upload.filename or "document").suffix.lower()
        safe_name = self._safe_filename(upload.filename or f"document-{uuid4().hex}{suffix}")
        saved_path = Path(self.settings.uploads_dir) / safe_name
        saved_path.write_bytes(content)

        extracted_text = self._extract_text(content, suffix)
        normalized_text = self._normalize_text(extracted_text)
        if len(normalized_text) < 50:
            raise HTTPException(status_code=400, detail="The document does not contain enough readable text to index.")

        chunks = self._chunk_text(normalized_text)
        collection_id = self._collection_id(safe_name, content)
        self._replace_existing_collection(collection_id)
        collection = self.client.get_or_create_collection(
            name=collection_id,
            embedding_function=self.embedding_function,
            metadata={"source": safe_name},
        )
        collection.upsert(
            ids=[f"{collection_id}-{index}" for index in range(len(chunks))],
            documents=chunks,
            metadatas=[{"source": safe_name, "chunk_index": index} for index in range(len(chunks))],
        )
        return {
            "collection_id": collection_id,
            "filename": safe_name,
            "chunks_indexed": len(chunks),
        }

    def list_collections(self) -> list[dict[str, str | int]]:
        collections: list[dict[str, str | int]] = []
        for collection in self.client.list_collections():
            metadata = collection.metadata or {}
            collections.append(
                {
                    "collection_id": collection.name,
                    "source": metadata.get("source", collection.name),
                    "chunks_indexed": collection.count(),
                }
            )

        collections.sort(key=lambda item: str(item["source"]).lower())
        return collections

    def chat(self, collection_id: str, question: str, history: list[ChatMessage]) -> dict[str, list[str] | str]:
        try:
            collection = self.client.get_collection(name=collection_id, embedding_function=self.embedding_function)
        except Exception as exc:
            raise HTTPException(status_code=404, detail="Document collection not found. Upload the file first.") from exc

        results = collection.query(query_texts=[question], n_results=4)
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        if not documents:
            raise HTTPException(status_code=404, detail="No indexed content found for this document.")

        context_blocks = []
        sources = []
        for document, metadata in zip(documents, metadatas, strict=False):
            source = metadata.get("source", "uploaded-file") if metadata else "uploaded-file"
            sources.append(source)
            context_blocks.append(f"Source: {source}\n{document}")

        answer = self.databricks_service.chat_with_context(
            question=question,
            context="\n\n".join(context_blocks),
            history=[message.model_dump() for message in history],
        )
        return {"answer": answer, "sources": list(dict.fromkeys(sources))}

    def _replace_existing_collection(self, collection_id: str) -> None:
        existing_collection_names = {collection.name for collection in self.client.list_collections()}
        if collection_id in existing_collection_names:
            self.client.delete_collection(name=collection_id)

    def _extract_text(self, content: bytes, suffix: str) -> str:
        if suffix == ".pdf":
            reader = PdfReader(BytesIO(content))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        if suffix == ".docx":
            document = Document(BytesIO(content))
            return "\n".join(paragraph.text for paragraph in document.paragraphs)
        if suffix in {".txt", ".md", ".doc"}:
            try:
                return content.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    return content.decode("latin-1")
                except UnicodeDecodeError as exc:
                    raise HTTPException(
                        status_code=400,
                        detail="Unsupported Word format. Use PDF, DOCX, TXT, or UTF-8 text-based DOC files.",
                    ) from exc
        raise HTTPException(status_code=400, detail="Unsupported file type. Upload PDF, DOCX, DOC, TXT, or MD.")

    def _chunk_text(self, text: str, chunk_size: int = 1200, overlap: int = 150) -> list[str]:
        chunks = []
        start = 0
        while start < len(text):
            end = min(len(text), start + chunk_size)
            chunks.append(text[start:end])
            if end == len(text):
                break
            start = max(0, end - overlap)
        return chunks

    def _normalize_text(self, text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text or "").strip()
        return cleaned

    def _safe_filename(self, filename: str) -> str:
        base_name = Path(filename).name.strip() or f"upload-{uuid4().hex}.txt"
        return re.sub(r"[^A-Za-z0-9._-]", "-", base_name)

    def _collection_id(self, filename: str, content: bytes) -> str:
        stem = re.sub(r"[^a-z0-9]+", "-", Path(filename).stem.lower()).strip("-")
        if len(stem) < 3:
            stem = f"document-{hashlib.sha1(filename.encode('utf-8')).hexdigest()[:8]}"
        return stem[:63].strip("-")