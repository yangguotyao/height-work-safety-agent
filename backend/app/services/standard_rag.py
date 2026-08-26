from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import chromadb
import pymupdf

from ..config import Settings
from ..db import Database
from .embedding_provider import EmbeddingProvider, build_embedding_provider
from .evidence_dedup import deduplicate_standard_evidence

STANDARD_FILES: dict[str, tuple[str, str]] = {
    "21086-2007 建筑幕墙.pdf": ("GB/T 21086-2007", "建筑幕墙"),
    "GB 51210-2016 建筑施工脚手架安全技术统一标准.pdf": (
        "GB 51210-2016",
        "建筑施工脚手架安全技术统一标准",
    ),
    "GB 55023-2022 施工脚手架通用规范.pdf": ("GB 55023-2022", "施工脚手架通用规范"),
    "GB_T 19155-2017 高处作业吊篮.pdf": ("GB/T 19155-2017", "高处作业吊篮"),
    "GB+2811-2019 安全帽.pdf": ("GB 2811-2019", "头部防护 安全帽"),
    "GB+3608-2025 高处作业分级.pdf": ("GB 3608-2025", "高处作业分级"),
    "GB+5725-2025 安全网.pdf": ("GB 5725-2025", "坠落防护 安全网"),
    "GB+6095-2021 安全带.pdf": ("GB 6095-2021", "坠落防护 安全带"),
    "JGJ130-2011 建筑施工扣件式钢管脚手架.pdf": (
        "JGJ 130-2011",
        "建筑施工扣件式钢管脚手架安全技术规范",
    ),
    "JGJ59—2011  建筑施工安全检查标准.pdf": ("JGJ 59-2011", "建筑施工安全检查标准"),
    "JGJ80-2016 建筑施工高处作业安全技术规范.pdf": (
        "JGJ 80-2016",
        "建筑施工高处作业安全技术规范",
    ),
}

CLAUSE_RE = re.compile(r"^\s*(\d+(?:\.\d+){1,4})(?:\s+|[、　])?(.*)$")


@dataclass(frozen=True)
class StandardChunk:
    id: str
    standard_id: str
    standard_code: str
    standard_name: str
    clause: str
    title_path: str
    page_start: int
    page_end: int
    text: str
    text_hash: str
    active: bool
    source_type: str


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _clean_pdf_text(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _split_text(text: str, max_chars: int = 1400) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    sentences = re.split(r"(?<=[。；！？])", text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current += sentence
    if current:
        chunks.append(current)
    return chunks


def _character_ngrams(text: str) -> set[str]:
    normalized = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text.lower())
    if len(normalized) < 2:
        return {normalized} if normalized else set()
    return {normalized[index : index + 2] for index in range(len(normalized) - 1)}


def _lexical_score(query: str, text: str) -> float:
    query_terms = _character_ngrams(query)
    text_terms = _character_ngrams(text)
    if not query_terms or not text_terms:
        return 0.0
    return len(query_terms & text_terms) / len(query_terms)


def _page_chunks(
    text: str,
    page_number: int,
    standard_id: str,
    standard_code: str,
    standard_name: str,
    active: bool,
) -> list[StandardChunk]:
    blocks: list[tuple[str, str]] = []
    current_clause = ""
    current_lines: list[str] = []
    for line in text.splitlines():
        match = CLAUSE_RE.match(line)
        if match and current_lines:
            blocks.append((current_clause, "\n".join(current_lines)))
            current_lines = []
        if match:
            current_clause = match.group(1)
        current_lines.append(line)
    if current_lines:
        blocks.append((current_clause, "\n".join(current_lines)))

    chunks: list[StandardChunk] = []
    for clause, block in blocks:
        if len(block.strip()) < 40:
            continue
        for part_index, part in enumerate(_split_text(block), start=1):
            prefix = f"{standard_code}｜{standard_name}｜{clause or '页内正文'}"
            content = f"{prefix}\n{part}"
            chunk_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            chunks.append(
                StandardChunk(
                    id=hashlib.sha256(
                        f"{standard_id}:{page_number}:{clause}:{part_index}:{chunk_hash}".encode()
                    ).hexdigest()[:32],
                    standard_id=standard_id,
                    standard_code=standard_code,
                    standard_name=standard_name,
                    clause=clause,
                    title_path=prefix,
                    page_start=page_number,
                    page_end=page_number,
                    text=content,
                    text_hash=chunk_hash,
                    active=active,
                    source_type="pdf_text",
                )
            )
    return chunks


class StandardRAGService:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database
        self.embedding: EmbeddingProvider = build_embedding_provider(settings)
        settings.resolved_chroma_dir.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(settings.resolved_chroma_dir))
        self.collection = self.client.get_or_create_collection(
            name=settings.standard_collection,
            metadata={"hnsw:space": "cosine"},
        )

    def _active_standard_info(self, standard_code: str) -> tuple[bool, str, str | None]:
        row = self.database.fetch_one(
            """SELECT standard_name, standard_status FROM audit_rules
               WHERE enabled_status = '启用' AND standard_code = ? LIMIT 1""",
            (standard_code,),
        )
        if row:
            return True, row["standard_name"], row["standard_status"]
        return False, "", "未纳入当前启用规则集"

    def _rule_fallback_chunks(
        self,
        standard_id: str,
        standard_code: str,
        standard_name: str,
        active: bool,
    ) -> list[StandardChunk]:
        rows = self.database.fetch_all(
            """SELECT clause, original_text, pdf_page FROM audit_rules
               WHERE enabled_status = '启用' AND standard_code = ?
               GROUP BY clause, original_text, pdf_page ORDER BY pdf_page, clause""",
            (standard_code,),
        )
        chunks = []
        for row in rows:
            content = f"{standard_code}｜{standard_name}｜{row['clause']}\n{row['original_text']}"
            text_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            page = int(row["pdf_page"] or 0)
            chunks.append(
                StandardChunk(
                    id=hashlib.sha256(
                        f"{standard_id}:fallback:{row['clause']}:{text_hash}".encode()
                    ).hexdigest()[:32],
                    standard_id=standard_id,
                    standard_code=standard_code,
                    standard_name=standard_name,
                    clause=row["clause"],
                    title_path=f"{standard_code}｜{standard_name}｜{row['clause']}",
                    page_start=page,
                    page_end=page,
                    text=content,
                    text_hash=text_hash,
                    active=active,
                    source_type="verified_rule_ocr_fallback",
                )
            )
        return chunks

    def reindex(self) -> dict[str, Any]:
        pdf_dir = self.settings.resolved_standard_pdf_dir
        if not pdf_dir.exists():
            raise FileNotFoundError(f"规范目录不存在：{pdf_dir}")
        try:
            self.client.delete_collection(self.settings.standard_collection)
        except Exception:
            pass
        self.collection = self.client.create_collection(
            name=self.settings.standard_collection,
            metadata={"hnsw:space": "cosine"},
        )
        with self.database.connect() as connection:
            connection.execute("DELETE FROM standard_chunks")
            connection.execute("DELETE FROM standards")

        now = datetime.now(timezone.utc).isoformat()
        all_chunks: list[StandardChunk] = []
        standard_rows = []
        summaries = []
        for pdf_path in sorted(pdf_dir.glob("*.pdf")):
            standard_code, default_name = STANDARD_FILES.get(
                pdf_path.name, (pdf_path.stem, pdf_path.stem)
            )
            active, rule_name, status = self._active_standard_info(standard_code)
            standard_name = rule_name or default_name
            standard_id = uuid4().hex
            document = pymupdf.open(pdf_path)
            page_texts = [_clean_pdf_text(page.get_text("text", sort=True)) for page in document]
            page_count = len(page_texts)
            document.close()
            text_page_count = sum(len(text) >= 50 for text in page_texts)
            chunks: list[StandardChunk] = []
            for page_number, text in enumerate(page_texts, start=1):
                if len(text) >= 50:
                    chunks.extend(
                        _page_chunks(
                            text,
                            page_number,
                            standard_id,
                            standard_code,
                            standard_name,
                            active,
                        )
                    )
            parse_status = "pdf_text"
            warning = None
            if text_page_count < max(1, page_count // 3):
                fallback = self._rule_fallback_chunks(
                    standard_id, standard_code, standard_name, active
                )
                chunks = fallback or chunks
                parse_status = "rule_ocr_fallback" if fallback else "needs_ocr"
                warning = "PDF 缺少有效文本层；已使用复核规则原文回填。" if fallback else "需 OCR。"
            file_hash = _sha256_path(pdf_path)
            standard_rows.append(
                (
                    standard_id,
                    standard_code,
                    standard_name,
                    status or "未纳入当前启用规则集",
                    int(active),
                    str(pdf_path),
                    file_hash,
                    page_count,
                    text_page_count,
                    parse_status,
                    warning,
                    now,
                )
            )
            all_chunks.extend(chunks)
            summaries.append(
                {
                    "standard_code": standard_code,
                    "active": active,
                    "pages": page_count,
                    "text_pages": text_page_count,
                    "chunks": len(chunks),
                    "parse_status": parse_status,
                }
            )

        self.database.executemany(
            """INSERT INTO standards
               (id, standard_code, standard_name, status, active, source_file, file_hash,
                page_count, text_page_count, parse_status, parse_warning, indexed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            standard_rows,
        )
        self.database.executemany(
            """INSERT INTO standard_chunks
               (id, standard_id, standard_code, standard_name, clause, title_path,
                page_start, page_end, text, text_hash, active, source_type, indexed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    chunk.id,
                    chunk.standard_id,
                    chunk.standard_code,
                    chunk.standard_name,
                    chunk.clause,
                    chunk.title_path,
                    chunk.page_start,
                    chunk.page_end,
                    chunk.text,
                    chunk.text_hash,
                    int(chunk.active),
                    chunk.source_type,
                    now,
                )
                for chunk in all_chunks
            ],
        )
        for start in range(0, len(all_chunks), 128):
            batch = all_chunks[start : start + 128]
            documents = [chunk.text for chunk in batch]
            self.collection.upsert(
                ids=[chunk.id for chunk in batch],
                documents=documents,
                embeddings=self.embedding.embed_documents(documents),
                metadatas=[
                    {
                        "standard_code": chunk.standard_code,
                        "standard_name": chunk.standard_name,
                        "clause": chunk.clause,
                        "page_start": chunk.page_start,
                        "page_end": chunk.page_end,
                        "active": chunk.active,
                        "source_type": chunk.source_type,
                    }
                    for chunk in batch
                ],
            )
        return {
            "standards": len(standard_rows),
            "chunks": len(all_chunks),
            "active_chunks": sum(chunk.active for chunk in all_chunks),
            "embedding_provider": self.embedding.provider_name,
            "embedding_model": self.embedding.model_name,
            "details": summaries,
        }

    def retrieve(
        self,
        query: str,
        standard_code: str | None = None,
        clause: str | None = None,
        expected_page: int | None = None,
        limit: int = 4,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if standard_code and clause:
            if expected_page and expected_page > 0:
                exact_rows = self.database.fetch_all(
                    """SELECT * FROM standard_chunks
                       WHERE active = 1 AND standard_code = ? AND clause = ?
                       ORDER BY ABS(page_start - ?), page_start LIMIT 1""",
                    (standard_code, clause, expected_page),
                )
            else:
                exact_rows = self.database.fetch_all(
                    """SELECT * FROM standard_chunks
                       WHERE active = 1 AND standard_code = ? AND clause = ?
                       ORDER BY page_start LIMIT 1""",
                    (standard_code, clause),
                )
            for row in exact_rows:
                results.append({**row, "score": 1.0, "retrieval_type": "exact_clause"})
            exact_results = deduplicate_standard_evidence(results, limit=limit)
            if exact_results:
                # A verified rule already binds the audit to a specific standard and
                # clause. Once that exact PDF passage is available, unrelated vector
                # neighbours only add noise and repeated-looking normative evidence.
                return exact_results
        try:
            count = self.collection.count()
            if count:
                conditions: list[dict[str, Any]] = [{"active": True}]
                if standard_code:
                    conditions.append({"standard_code": standard_code})
                where: dict[str, Any] = (
                    conditions[0] if len(conditions) == 1 else {"$and": conditions}
                )
                response = self.collection.query(
                    query_embeddings=[self.embedding.embed_query(query)],
                    n_results=min(max(limit, 1), count),
                    where=where,
                    include=["documents", "metadatas", "distances"],
                )
                for chunk_id, document, metadata, distance in zip(
                    response["ids"][0],
                    response["documents"][0],
                    response["metadatas"][0],
                    response["distances"][0],
                    strict=True,
                ):
                    results.append(
                        {
                            "id": chunk_id,
                            "standard_code": metadata["standard_code"],
                            "standard_name": metadata["standard_name"],
                            "clause": metadata["clause"],
                            "page_start": metadata["page_start"],
                            "page_end": metadata["page_end"],
                            "text": document,
                            "source_type": metadata["source_type"],
                            "score": round(max(0.0, 1.0 - float(distance)), 4),
                            "retrieval_type": "vector",
                        }
                    )
        except Exception:
            # A corrupt or temporarily unavailable vector index must not fail the audit.
            # Exact clause hits above are retained and a deterministic lexical fallback is used.
            sql = "SELECT * FROM standard_chunks WHERE active = 1"
            params: tuple[Any, ...] = ()
            if standard_code:
                sql += " AND standard_code = ?"
                params = (standard_code,)
            candidates = self.database.fetch_all(sql, params)
            scored = sorted(
                ((_lexical_score(query, row["text"]), row) for row in candidates),
                key=lambda item: item[0],
                reverse=True,
            )
            for score, row in scored[: max(limit, 1)]:
                if score <= 0:
                    continue
                results.append(
                    {**row, "score": round(score, 4), "retrieval_type": "lexical_fallback"}
                )
        return deduplicate_standard_evidence(results, limit=limit)

    def discover(self, query: str, limit: int = 4) -> list[dict[str, Any]]:
        """Plan-driven hybrid discovery over a wider Chroma candidate pool.

        Chroma supplies recall. A lightweight lexical rerank and a preference for
        addressable clauses reduce explanatory-page and corrupted-text noise. No
        rule code, requirement or expected clause is used here.
        """
        pool_limit = max(limit * 5, 20)
        candidates = self.retrieve(query, limit=pool_limit)
        structured = [item for item in candidates if str(item.get("clause") or "").strip()]
        # Returning fewer addressable clauses is safer than padding Top-K with
        # unaddressable explanation pages or OCR noise.
        ranked_pool = structured
        ranked: list[dict[str, Any]] = []
        for item in ranked_pool:
            vector_score = float(item.get("score") or 0.0)
            lexical_score = _lexical_score(query, str(item.get("text") or ""))
            clause_bonus = 0.06 if str(item.get("clause") or "").strip() else 0.0
            hybrid_score = min(
                1.0, 0.58 * vector_score + 0.36 * lexical_score + clause_bonus
            )
            ranked.append(
                {
                    **item,
                    "score": round(hybrid_score, 4),
                    "vector_score": round(vector_score, 4),
                    "lexical_score": round(lexical_score, 4),
                    "retrieval_type": "scene_vector",
                }
            )
        ranked.sort(
            key=lambda item: (
                -float(item.get("score") or 0.0),
                -float(item.get("vector_score") or 0.0),
                str(item.get("standard_code") or ""),
                str(item.get("clause") or ""),
            )
        )
        return deduplicate_standard_evidence(ranked, limit=limit)

    def vector_count(self) -> int:
        try:
            return self.collection.count()
        except Exception:
            return 0
