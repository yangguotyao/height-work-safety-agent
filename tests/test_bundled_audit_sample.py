from pathlib import Path

from backend.app.db import Database
from backend.app.repositories import Repository
from backend.app.services.document_service import DocumentService


def test_bundled_docx_sample_keeps_display_name_and_parses(test_settings):
    database = Database(test_settings.resolved_database_path)
    database.initialize()
    repository = Repository(database)
    service = DocumentService(repository, test_settings)
    source = (
        Path(__file__).resolve().parents[1]
        / "tests"
        / "fixtures"
        / "脚手架工程施工方案_预置.docx"
    )

    document = service.save_bundled_document(
        source, display_name="脚手架工程施工方案.doc"
    )
    parsed = service.parse_document(document["id"])

    assert document["filename"] == "脚手架工程施工方案.doc"
    assert Path(document["storage_path"]).suffix == ".docx"
    assert parsed["status"] == "parsed"
    assert parsed["segment_count"] > 100
