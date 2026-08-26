from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from ..config import Settings
from ..parsers.docx_parser import parse_docx, validate_docx
from ..parsers.legacy_doc_parser import convert_doc_to_docx, validate_legacy_doc
from ..repositories import Repository


class DocumentService:
    def __init__(self, repository: Repository, settings: Settings):
        self.repository = repository
        self.settings = settings

    async def save_upload(self, upload: UploadFile) -> dict:
        original_name = Path(upload.filename or "").name
        suffix = Path(original_name).suffix.lower()
        if not original_name or suffix not in {".docx", ".doc"}:
            raise ValueError("施工方案只接受 .docx 或 .doc")
        upload_dir = self.settings.resolved_upload_dir
        upload_dir.mkdir(parents=True, exist_ok=True)
        storage_path = upload_dir / f"{uuid4().hex}{suffix}"
        digest = hashlib.sha256()
        total = 0
        maximum = self.settings.max_upload_mb * 1024 * 1024
        try:
            with storage_path.open("wb") as target:
                while chunk := await upload.read(1024 * 1024):
                    total += len(chunk)
                    if total > maximum:
                        raise ValueError(f"文件超过 {self.settings.max_upload_mb}MB 限制")
                    digest.update(chunk)
                    target.write(chunk)
            if suffix == ".docx":
                validate_docx(storage_path)
            else:
                validate_legacy_doc(storage_path)
        except Exception:
            storage_path.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()
        return self.repository.create_document(
            filename=original_name,
            sha256=digest.hexdigest(),
            storage_path=str(storage_path),
        )

    def save_bundled_document(self, source: Path, *, display_name: str) -> dict:
        """Copy one allowlisted bundled example into the active project workspace."""
        source = source.resolve()
        suffix = source.suffix.lower()
        if not source.is_file() or suffix not in {".docx", ".doc"}:
            raise FileNotFoundError("预置施工方案不存在")
        if source.stat().st_size > self.settings.max_upload_mb * 1024 * 1024:
            raise ValueError(f"文件超过 {self.settings.max_upload_mb}MB 限制")

        upload_dir = self.settings.resolved_upload_dir
        upload_dir.mkdir(parents=True, exist_ok=True)
        storage_path = upload_dir / f"{uuid4().hex}{suffix}"
        digest = hashlib.sha256()
        try:
            with source.open("rb") as input_stream, storage_path.open("wb") as output_stream:
                for chunk in iter(lambda: input_stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                    output_stream.write(chunk)
            if suffix == ".docx":
                validate_docx(storage_path)
            else:
                validate_legacy_doc(storage_path)
        except Exception:
            storage_path.unlink(missing_ok=True)
            raise
        return self.repository.create_document(
            filename=Path(display_name).name,
            sha256=digest.hexdigest(),
            storage_path=str(storage_path),
        )

    def parse_document(self, document_id: str) -> dict:
        document = self.repository.get_document(document_id)
        source_path = Path(document["storage_path"])
        if source_path.suffix.lower() == ".doc":
            converted = convert_doc_to_docx(
                source_path, self.settings.resolved_upload_dir.parent / "conversions"
            )
            parsed = parse_docx(converted)
            parsed.warnings.append(
                "原始 DOC 已通过 Microsoft Word 转换为临时 DOCX 后解析；原始文件保持不变。"
            )
        else:
            parsed = parse_docx(source_path)
        self.repository.save_parsed_document(document_id, parsed)
        return self.repository.get_document(document_id)
