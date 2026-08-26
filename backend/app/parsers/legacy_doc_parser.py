from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from uuid import uuid4

OLE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")


def validate_legacy_doc(path: Path) -> None:
    if path.suffix.lower() != ".doc":
        raise ValueError("文件不是旧版 .doc 文档")
    with path.open("rb") as stream:
        signature = stream.read(8)
    if signature != OLE_SIGNATURE:
        raise ValueError(".doc 扩展名与文件内容不匹配，文件可能已损坏")


def convert_doc_to_docx(source: Path, output_dir: Path) -> Path:
    validate_legacy_doc(source)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = (output_dir / f"{source.stem}-{uuid4().hex}.docx").resolve()
    source = source.resolve()
    worker = Path(__file__).with_name("word_doc_converter_worker.py")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [sys.executable, str(worker), str(source), str(target)],
            capture_output=True,
            check=False,
            creationflags=creation_flags,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        target.unlink(missing_ok=True)
        raise ValueError("Microsoft Word 转换 DOC 超时（120 秒）") from exc
    if result.returncode != 0 or not target.exists():
        target.unlink(missing_ok=True)
        detail = result.stderr.strip() or "Microsoft Word 未生成转换后的 DOCX"
        raise ValueError(f"Microsoft Word 无法转换该 DOC：{detail}")
    return target
