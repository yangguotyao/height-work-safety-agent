from backend.app.db import Database
from backend.app.services.rule_importer import import_rules
from backend.app.services.standard_rag import StandardRAGService


def test_indexes_all_standard_pdfs_and_retrieves_exact_clause(test_settings):
    database = Database(test_settings.resolved_database_path)
    database.initialize()
    import_rules(database, test_settings.resolved_rule_workbook_path)
    rag = StandardRAGService(test_settings, database)

    summary = rag.reindex()
    results = rag.retrieve(
        "结构临边防护栏杆立杆间距不应大于2m",
        standard_code="JGJ 80-2016",
        clause="4.3.1",
        limit=4,
    )

    assert summary["standards"] == 11
    assert summary["chunks"] > 500
    assert summary["active_chunks"] > 100
    assert results[0]["standard_code"] == "JGJ 80-2016"
    assert results[0]["clause"] == "4.3.1"
    assert results[0]["retrieval_type"] == "exact_clause"
    assert "立杆间距不应大于2m" in results[0]["text"]
    assert all(item["retrieval_type"] == "exact_clause" for item in results)
    assert len(results) == 1

    page_targeted = rag.retrieve(
        "结构临边防护栏杆立杆间距不应大于2m",
        standard_code="JGJ 80-2016",
        clause="4.3.1",
        expected_page=results[0]["page_start"],
        limit=4,
    )
    assert len(page_targeted) == 1
    assert page_targeted[0]["page_start"] == results[0]["page_start"]

    discovered = rag.discover(
        "洞口作业 楼板开洞，孔洞小于等于300mm时钢筋不断开，大于300mm时洞边加筋。",
        limit=4,
    )
    assert any(
        item["standard_code"] == "JGJ 80-2016" and item["clause"] == "4.2.1"
        for item in discovered
    )
    assert all(item["clause"] for item in discovered)
    assert all(item["retrieval_type"] == "scene_vector" for item in discovered)

    class BrokenVectorCollection:
        def count(self):
            raise RuntimeError("simulated unavailable vector index")

    rag.collection = BrokenVectorCollection()
    fallback = rag.retrieve(
        "防护栏杆立杆间距不应大于2m",
        standard_code="JGJ 80-2016",
        limit=2,
    )
    assert fallback
    assert fallback[0]["retrieval_type"] == "lexical_fallback"
