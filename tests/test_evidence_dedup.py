from backend.app.services.evidence_dedup import deduplicate_standard_evidence


def test_same_clause_text_with_different_internal_ids_is_deduplicated():
    entries = [
        {
            "id": "exact-row",
            "standard_code": "JGJ 80-2016",
            "clause": "4.3.1",
            "page_start": 20,
            "text": "JGJ 80-2016｜建筑施工高处作业安全技术规范｜4.3.1\n防护栏杆立杆间距不应大于2m。",
        },
        {
            "id": "vector-row",
            "standard_code": "JGJ 80-2016",
            "clause": "4.3.1",
            "page_start": 20,
            "text": "JGJ 80-2016｜建筑施工高处作业安全技术规范｜4.3.1\n防护栏杆立杆间距不应大于2m。",
        },
    ]

    assert len(deduplicate_standard_evidence(entries)) == 1


def test_different_passages_in_same_clause_are_preserved():
    entries = [
        {
            "id": "part-1",
            "standard_code": "JGJ 80-2016",
            "clause": "4.3.1",
            "page_start": 20,
            "text": "防护栏杆立杆间距不应大于2m。",
        },
        {
            "id": "part-2",
            "standard_code": "JGJ 80-2016",
            "clause": "4.3.1",
            "page_start": 20,
            "text": "栏杆下边应设置严密固定的挡脚板。",
        },
    ]

    assert len(deduplicate_standard_evidence(entries)) == 2
