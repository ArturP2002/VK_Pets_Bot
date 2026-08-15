"""Chroma RAG retrieval over formulary chunks."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger(__name__)


@dataclass
class RagChunk:
    text: str
    drug_id: int | None
    section: str
    source: str
    score: float
    drug_name: str = ""


@lru_cache(maxsize=1)
def _collection(chroma_dir: str):
    import chromadb
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

    # Prefer local HF cache so runtime does not require network.
    embedding_fn = SentenceTransformerEmbeddingFunction(
        model_name=config.FORMULARY_EMBEDDING_MODEL,
        local_files_only=True,
    )
    client = chromadb.PersistentClient(path=chroma_dir)
    return client.get_or_create_collection(
        name=config.FORMULARY_CHROMA_COLLECTION,
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"},
    )


def retrieve(
    query: str,
    *,
    drug_id: int | None = None,
    top_k: int | None = None,
    sections: list[str] | None = None,
    chroma_dir: str | Path | None = None,
) -> list[RagChunk]:
    """
    Semantic retrieval. If drug_id is set, results are filtered to that drug.
    """
    q = (query or "").strip()
    if not q:
        return []
    path = str(chroma_dir or config.FORMULARY_CHROMA_DIR)
    if not Path(path).exists():
        logger.warning("Chroma dir missing: %s", path)
        return []

    k = top_k or config.FORMULARY_RAG_TOP_K
    where: dict[str, Any] | None = None
    if drug_id is not None and sections:
        where = {
            "$and": [
                {"drug_id": int(drug_id)},
                {"section": {"$in": list(sections)}},
            ]
        }
    elif drug_id is not None:
        where = {"drug_id": int(drug_id)}
    elif sections:
        where = {"section": {"$in": list(sections)}}

    try:
        collection = _collection(path)
        result = collection.query(
            query_texts=[q],
            n_results=k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
    except Exception:
        logger.exception("Chroma query failed")
        return []

    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]
    chunks: list[RagChunk] = []
    for doc, meta, dist in zip(docs, metas, dists):
        meta = meta or {}
        # cosine distance → similarity-ish score
        score = 1.0 - float(dist) if dist is not None else 0.0
        chunks.append(
            RagChunk(
                text=doc or "",
                drug_id=int(meta["drug_id"]) if meta.get("drug_id") is not None else None,
                section=str(meta.get("section") or ""),
                source=str(meta.get("source") or ""),
                score=score,
                drug_name=str(meta.get("drug_name") or ""),
            )
        )
    return chunks


def format_chunks_for_prompt(chunks: list[RagChunk]) -> str:
    if not chunks:
        return ""
    parts = []
    for i, chunk in enumerate(chunks, 1):
        parts.append(
            f"[{i}] drug_id={chunk.drug_id} section={chunk.section} "
            f"score={chunk.score:.3f}\n{chunk.text}"
        )
    return "\n\n".join(parts)


def reset_collection_cache() -> None:
    _collection.cache_clear()
